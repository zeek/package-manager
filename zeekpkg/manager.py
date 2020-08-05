"""
A module defining the main Zeek Package Manager interface which supplies
methods to interact with and operate on Zeek packages.
"""

import os
import sys
import copy
import json
import shutil
import filecmp
import tarfile
import subprocess

try:
    from backports import configparser
except ImportError:
    import configparser

try:
    from urllib.parse import urlparse
except ImportError:
    from urlparse import urlparse

if ( sys.version_info[0] < 3 or
     (sys.version_info[0] == 3 and sys.version_info[1] < 2) ):
    from configparser import SafeConfigParser as GoodConfigParser
else:
    # SafeConfigParser renamed to ConfigParser in Python >= 3.2
    from configparser import ConfigParser as GoodConfigParser

import git
import semantic_version as semver

from collections import deque

from ._util import (
    make_dir,
    delete_path,
    make_symlink,
    copy_over_path,
    git_checkout,
    git_clone,
    is_sha1,
    get_zeek_version,
    std_encoding,
    find_program,
    read_zeek_config_line,
)
from .source import (
    AGGREGATE_DATA_FILE,
    Source
)
from .package import (
    METADATA_FILENAME,
    LEGACY_METADATA_FILENAME,
    TRACKING_METHOD_VERSION,
    TRACKING_METHOD_BRANCH,
    TRACKING_METHOD_COMMIT,
    PLUGIN_MAGIC_FILE,
    PLUGIN_MAGIC_FILE_DISABLED,
    name_from_path,
    aliases,
    user_vars,
    canonical_url,
    is_valid_name as is_valid_package_name,
    Package,
    PackageInfo,
    PackageStatus,
    InstalledPackage
)
from . import (
    __version__,
    LOG,
)


class Manager(object):
    """A package manager object performs various operations on packages.

    It uses a state directory and a manifest file within it to keep
    track of package sources, installed packages and their statuses.

    Attributes:
        sources (dict of str -> :class:`.source.Source`): dictionary package
            sources keyed by the name given to :meth:`add_source()`

        installed_pkgs (dict of str -> :class:`.package.InstalledPackage`):
            a dictionary of installed packaged keyed on package names (the last
            component of the package's git URL)

        zeek_dist (str): path to the Zeek source code distribution.  This
            is needed for packages that contain Zeek plugins that need to be
            built from source code.

        state_dir (str): the directory where the package manager will
            a maintain manifest file, package/source git clones, and other
            persistent state the manager needs in order to operate

        user_vars (dict of str -> str): dictionary of key-value pairs where
            the value will be substituted into package build commands in place
            of the key.

        backup_dir (str): a directory where the package manager will
            store backup files (e.g. locally modified package config files)

        log_dir (str): a directory where the package manager will
            store misc. logs files (e.g. package build logs)

        scratch_dir (str): a directory where the package manager performs
            miscellaneous/temporary file operations

        script_dir (str): the directory where the package manager will
            copy each installed package's `script_dir` (as given by its
            :file:`zkg.meta` or :file:`bro-pkg.meta`).  Each package gets a
            subdirectory within `script_dir` associated with its name.

        plugin_dir (str): the directory where the package manager will
            copy each installed package's `plugin_dir` (as given by its
            :file:`zkg.meta` or :file:`bro-pkg.meta`).  Each package gets a
            subdirectory within `plugin_dir` associated with its name.

        source_clonedir (str): the directory where the package manager
            will clone package sources.  Each source gets a subdirectory
            associated with its name.

        package_clonedir (str): the directory where the package manager
            will clone installed packages.  Each package gets a subdirectory
            associated with its name.

        package_testdir (str): the directory where the package manager
            will run tests.  Each package gets a subdirectory
            associated with its name.

        manifest (str): the path to the package manager's manifest file.
            This file maintains a list of installed packages and their status.

        autoload_script (str): path to a Zeek script named :file:`packages.zeek`
            that the package manager maintains.  It is a list of ``@load`` for
            each installed package that is marked as loaded (see
            :meth:`load()`).

        autoload_package (str): path to a Zeek :file:`__load__.zeek` script
            which is just a symlink to `autoload_script`.  It's always located
            in a directory named :file:`packages`, so as long as
            :envvar:`ZEEKPATH` is configured correctly, ``@load packages`` will
            load all installed packages that have been marked as loaded.
    """

    def __init__(self, state_dir, script_dir, plugin_dir, zeek_dist='',
                 user_vars=None):
        """Creates a package manager instance.

        Args:
            state_dir (str): value to set the `state_dir` attribute to

            script_dir (str): value to set the `script_dir` attribute to

            plugin_dir (str): value to set the `plugin_dir` attribute to

            zeek_dist (str): value to set the `zeek_dist` attribute to

            user_vars (dict of str -> str): key-value pair substitutions for
                use in package build commands.

        Raises:
            OSError: when a package manager state directory can't be created
            IOError: when a package manager state file can't be created
        """
        LOG.debug('init Manager version %s', __version__)
        self.sources = {}
        self.installed_pkgs = {}
        # The bro_dist attribute exists just for backward compatibility
        self.bro_dist = zeek_dist
        self.zeek_dist = zeek_dist
        self.state_dir = state_dir
        self.user_vars = {} if user_vars is None else user_vars
        self.backup_dir = os.path.join(self.state_dir, 'backups')
        self.log_dir = os.path.join(self.state_dir, 'logs')
        self.scratch_dir = os.path.join(self.state_dir, 'scratch')
        self._script_dir = script_dir
        self.script_dir = os.path.join(script_dir, 'packages')
        self._plugin_dir = plugin_dir
        self.plugin_dir = os.path.join(plugin_dir, 'packages')
        self.source_clonedir = os.path.join(self.state_dir, 'clones', 'source')
        self.package_clonedir = os.path.join(
            self.state_dir, 'clones', 'package')
        self.package_testdir = os.path.join(self.state_dir, 'testing')
        self.manifest = os.path.join(self.state_dir, 'manifest.json')
        self.autoload_script = os.path.join(self.script_dir, 'packages.zeek')
        self.autoload_package = os.path.join(self.script_dir, '__load__.zeek')
        make_dir(self.state_dir)
        make_dir(self.log_dir)
        make_dir(self.scratch_dir)
        make_dir(self.source_clonedir)
        make_dir(self.package_clonedir)
        make_dir(self.script_dir)
        make_dir(self.plugin_dir)
        _create_readme(os.path.join(self.script_dir, 'README'))
        _create_readme(os.path.join(self.plugin_dir, 'README'))

        if not os.path.exists(self.manifest):
            self._write_manifest()

        prev_script_dir, prev_plugin_dir = self._read_manifest()

        if os.path.realpath(prev_script_dir) != os.path.realpath(self.script_dir):
            LOG.info('relocating script_dir %s -> %s', prev_script_dir,
                     self.script_dir)

            if os.path.exists(prev_script_dir):
                delete_path(self.script_dir)
                shutil.move(prev_script_dir, self.script_dir)

            prev_zeekpath = os.path.dirname(prev_script_dir)

            for pkg_name in self.installed_pkgs:
                old_link = os.path.join(prev_zeekpath, pkg_name)
                new_link = os.path.join(self.zeekpath(), pkg_name)

                if os.path.lexists(old_link):
                    LOG.info('moving package link %s -> %s',
                             old_link, new_link)
                    shutil.move(old_link, new_link)
                else:
                    LOG.info('skip moving package link %s -> %s',
                             old_link, new_link)

            self._write_manifest()

        if os.path.realpath(prev_plugin_dir) != os.path.realpath(self.plugin_dir):
            LOG.info('relocating plugin_dir %s -> %s', prev_plugin_dir,
                     self.plugin_dir)

            if os.path.exists(prev_plugin_dir):
                delete_path(self.plugin_dir)
                shutil.move(prev_plugin_dir, self.plugin_dir)

            self._write_manifest()

        self._write_autoloader()
        make_symlink('packages.zeek', self.autoload_package)

        # Backward compatibility (Pre-Zeek 3.0 does not handle .zeek files)
        autoload_script_fallback = os.path.join(self.script_dir, 'packages.bro')
        autoload_package_fallback = os.path.join(self.script_dir, '__load__.bro')
        delete_path(autoload_script_fallback)
        delete_path(autoload_package_fallback)
        make_symlink('packages.zeek', autoload_script_fallback)
        make_symlink('packages.zeek', autoload_package_fallback)

    def _write_autoloader(self):
        """Write the :file:`packages.zeek` loader script.

        Raises:
            IOError: if :file:`packages.zeek` loader script cannot be written
        """
        with open(self.autoload_script, 'w') as f:
            content = ('# WARNING: This file is managed by zkg.\n'
                       '# Do not make direct modifications here.\n')

            for ipkg in self.loaded_packages():
                if self.has_scripts(ipkg):
                    content += '@load ./{}\n'.format(ipkg.package.name)

            f.write(content)

    def _write_plugin_magic(self, ipkg):
        """Enables/disables any Zeek plugin included with a package.

        Zeek's plugin code scans its plugin directories for
        __bro_plugin__ magic files, which indicate presence of a
        plugin directory. When this file does not exist, Zeek does not
        recognize a plugin.

        When we're loading a package, this function renames an
        existing __bro_plugin__.disabled file to __bro_plugin__, and
        vice versa when we're unloading a package.

        When the package doesn't include a plugin, or when the plugin
        directory already contains a correctly named magic file, this
        function does nothing.
        """
        magic_path = os.path.join(
            self.plugin_dir, ipkg.package.name, PLUGIN_MAGIC_FILE)
        magic_path_disabled = os.path.join(
            self.plugin_dir, ipkg.package.name, PLUGIN_MAGIC_FILE_DISABLED)

        if ipkg.status.is_loaded:
            if os.path.exists(magic_path_disabled):
                try:
                    os.rename(magic_path_disabled, magic_path)
                except OSError as exception:
                    LOG.warning('could not enable plugin: %s: %s'.format(
                        type(exception).__name__, exception))
        else:
            if os.path.exists(magic_path):
                try:
                    os.rename(magic_path, magic_path_disabled)
                except OSError as exception:
                    LOG.warning('could not disable plugin: %s: %s'.format(
                        type(exception).__name__, exception))

    def _read_manifest(self):
        """Read the manifest file containing the list of installed packages.

        Returns:
            tuple: (previous script_dir, previous plugin_dir)

        Raises:
            IOError: when the manifest file can't be read
        """
        with open(self.manifest, 'r') as f:
            data = json.load(f)
            version = data['manifest_version']
            pkg_list = data['installed_packages']
            self.installed_pkgs = {}

            for dicts in pkg_list:
                pkg_dict = dicts['package_dict']
                status_dict = dicts['status_dict']
                pkg_name = pkg_dict['name']

                if version == 0 and 'index_data' in pkg_dict:
                    del pkg_dict['index_data']

                pkg_dict['canonical'] = True;
                pkg = Package(**pkg_dict)
                status = PackageStatus(**status_dict)
                self.installed_pkgs[pkg_name] = InstalledPackage(pkg, status)

            return data['script_dir'], data['plugin_dir']

    def _write_manifest(self):
        """Writes the manifest file containing the list of installed packages.

        Raises:
            IOError: when the manifest file can't be written
        """
        pkg_list = []

        for _, installed_pkg in self.installed_pkgs.items():
            pkg_list.append({'package_dict': installed_pkg.package.__dict__,
                             'status_dict': installed_pkg.status.__dict__})

        data = {'manifest_version': 1, 'script_dir': self.script_dir,
                'plugin_dir': self.plugin_dir, 'installed_packages': pkg_list}

        with open(self.manifest, 'w') as f:
            json.dump(data, f, indent=2, sort_keys=True)

    def zeekpath(self):
        """Return the path where installed package scripts are located.

        This path can be added to :envvar:`ZEEKPATH` for interoperability with
        Zeek.
        """
        return os.path.dirname(self.script_dir)

    def bropath(self):
        """Same as :meth:`zeekpath`.

        Using :meth:`zeekpath` is preferred since this may later be deprecated.
        """
        return self.zeekpath()

    def zeek_plugin_path(self):
        """Return the path where installed package plugins are located.

        This path can be added to :envvar:`ZEEK_PLUGIN_PATH` for
        interoperability with Zeek.
        """
        return os.path.dirname(self.plugin_dir)

    def bro_plugin_path(self):
        """Same as :meth:`zeek_plugin_path`.

        Using :meth:`zeek_plugin_path` is preferred since this may later be
        deprecated.
        """
        return self.zeek_plugin_path()

    def add_source(self, name, git_url):
        """Add a git repository that acts as a source of packages.

        Args:
            name (str): a short name that will be used to reference the package
                source.

            git_url (str): the git URL of the package source

        Returns:
            str: empty string if the source is successfully added, else the
            reason why it failed.
        """
        if name in self.sources:
            existing_source = self.sources[name]

            if existing_source.git_url == git_url:
                LOG.debug('duplicate source "%s"', name)
                return True

            return 'source already exists with different URL: {}'.format(
                existing_source.git_url)

        clone_path = os.path.join(self.source_clonedir, name)

        # Support @ in the path to denote the "version" to checkout
        version = None
        parse_result = urlparse(git_url)

        if parse_result.path and '@' in parse_result.path:
            git_url, version = git_url.rsplit('@', 1)

        try:
            source = Source(
                name=name,
                clone_path=clone_path,
                git_url=git_url,
                version=version,
            )
        except git.exc.GitCommandError as error:
            LOG.warning('failed to clone git repo: %s', error)
            return 'failed to clone git repo'
        else:
            self.sources[name] = source

        return ''

    def source_packages(self):
        """Return a list of :class:`.package.Package` within all sources."""
        rval = []

        for _, source in self.sources.items():
            rval += source.packages()

        return rval

    def installed_packages(self):
        """Return list of :class:`.package.InstalledPackage`."""
        return [ipkg for _, ipkg in sorted(self.installed_pkgs.items())]

    def installed_package_dependencies(self):
        """Return dict of package name -> :class:`.package.InstalledPackage`."""
        return {name: ipkg.package.dependencies()
                for name, ipkg in self.installed_pkgs.items()}

    def loaded_packages(self):
        """Return list of loaded :class:`.package.InstalledPackage`."""
        rval = []

        for _, ipkg in sorted(self.installed_pkgs.items()):
            if ipkg.status.is_loaded:
                rval.append(ipkg)

        return rval

    def package_build_log(self, pkg_path):
        """Return the path to the package manager's build log for a package.

        Args:
            pkg_path (str): the full git URL of a package or the shortened
                path/name that refers to it within a package source.  E.g. for
                a package source called "zeek" with package named "foo" in
                :file:`alice/zkg.index`, the following inputs may refer
                to the package: "foo", "alice/foo", or "zeek/alice/foo".
        """
        name = name_from_path(pkg_path)
        return os.path.join(self.log_dir, '{}-build.log'.format(name))

    def match_source_packages(self, pkg_path):
        """Return a list of :class:`.package.Package` that match a given path.

        Args:
            pkg_path (str): the full git URL of a package or the shortened
                path/name that refers to it within a package source.  E.g. for
                a package source called "zeek" with package named "foo" in
                :file:`alice/zkg.index`, the following inputs may refer
                to the package: "foo", "alice/foo", or "zeek/alice/foo".
        """
        rval = []
        canon_url = canonical_url(pkg_path)

        for pkg in self.source_packages():
            if pkg.matches_path(canon_url):
                rval.append(pkg)

        return rval

    def find_installed_package(self, pkg_path):
        """Return an :class:`.package.InstalledPackage` if one matches the name.

        Args:
            pkg_path (str): the full git URL of a package or the shortened
                path/name that refers to it within a package source.  E.g. for
                a package source called "zeek" with package named "foo" in
                :file:`alice/zkg.index`, the following inputs may refer
                to the package: "foo", "alice/foo", or "zeek/alice/foo".
        """
        pkg_name = name_from_path(pkg_path)
        return self.installed_pkgs.get(pkg_name)

    def get_installed_package_dependencies(self, pkg_path):
        """Return a set of tuples of dependent package names and their version
        number if pkg_path is an installed package.

        Args:
            pkg_path (str): the full git URL of a package or the shortened
                path/name that refers to it within a package source.  E.g. for
                a package source called "zeek" with package named "foo" in
                :file:`alice/zkg.index`, the following inputs may refer
                to the package: "foo", "alice/foo", or "zeek/alice/foo".
        """
        ipkg = self.find_installed_package(pkg_path)

        if ipkg:
            return ipkg.package.dependencies()

        return None

    def has_scripts(self, installed_pkg):
        """Return whether a :class:`.package.InstalledPackage` installed scripts.

        Args:
            installed_pkg(:class:`.package.InstalledPackage`): the installed
                package to check for whether it has installed any Zeek scripts.

        Returns:
            bool: True if the package has installed Zeek scripts.
        """
        return os.path.exists(os.path.join(self.script_dir,
                                           installed_pkg.package.name))

    def has_plugin(self, installed_pkg):
        """Return whether a :class:`.package.InstalledPackage` installed a plugin.

        Args:
            installed_pkg(:class:`.package.InstalledPackage`): the installed
                package to check for whether it has installed a Zeek plugin.

        Returns:
            bool: True if the package has installed a Zeek plugin.
        """
        return os.path.exists(os.path.join(self.plugin_dir,
                                           installed_pkg.package.name))

    def save_temporary_config_files(self, installed_pkg):
        """Return a list of temporary package config file backups.

        Args:
            installed_pkg(:class:`.package.InstalledPackage`): the installed
                package to save temporary config file backups for.

        Returns:
            list of (str, str): tuples that describe the config files backups.
            The first element is the config file as specified in the package
            metadata (a file path relative to the package's root directory).
            The second element is an absolute file system path to where that
            config file has been copied.  It should be considered temporary,
            so make use of it before doing any further operations on packages.
        """
        import re
        metadata = installed_pkg.package.metadata
        config_files = re.split(',\s*', metadata.get('config_files', ''))

        if not config_files:
            return []

        pkg_name = installed_pkg.package.name
        clone_dir = os.path.join(self.package_clonedir, pkg_name)
        rval = []

        for config_file in config_files:
            config_file_path = os.path.join(clone_dir, config_file)

            if not os.path.isfile(config_file_path):
                LOG.info("package '%s' claims config file at '%s',"
                         " but it does not exist", pkg_name, config_file)
                continue

            backup_file = os.path.join(self.scratch_dir, 'tmpcfg', config_file)
            make_dir(os.path.dirname(backup_file))
            shutil.copy2(config_file_path, backup_file)
            rval.append((config_file, backup_file))

        return rval

    def modified_config_files(self, installed_pkg):
        """Return a list of package config files that the user has modified.

        Args:
            installed_pkg(:class:`.package.InstalledPackage`): the installed
                package to check for whether it has installed any Zeek scripts.

        Returns:
            list of (str, str): tuples that describe the modified config files.
            The first element is the config file as specified in the package
            metadata (a file path relative to the package's root directory).
            The second element is an absolute file system path to where that
            config file is currently installed.
        """
        import re
        metadata = installed_pkg.package.metadata
        config_files = re.split(',\s*', metadata.get('config_files', ''))

        if not config_files:
            return []

        pkg_name = installed_pkg.package.name
        script_install_dir = os.path.join(self.script_dir, pkg_name)
        plugin_install_dir = os.path.join(self.plugin_dir, pkg_name)
        clone_dir = os.path.join(self.package_clonedir, pkg_name)
        script_dir = metadata.get('script_dir', '')
        plugin_dir = metadata.get('plugin_dir', 'build')
        rval = []

        for config_file in config_files:
            their_config_file_path = os.path.join(clone_dir, config_file)

            if not os.path.isfile(their_config_file_path):
                LOG.info("package '%s' claims config file at '%s',"
                         " but it does not exist", pkg_name, config_file)
                continue

            if config_file.startswith(plugin_dir):
                our_config_file_path = os.path.join(
                    plugin_install_dir, config_file[len(plugin_dir):])

                if not os.path.isfile(our_config_file_path):
                    LOG.info("package '%s' config file '%s' not found"
                             " in plugin_dir: %s", pkg_name, config_file,
                             our_config_file_path)
                    continue
            elif config_file.startswith(script_dir):
                our_config_file_path = os.path.join(
                    script_install_dir, config_file[len(script_dir):])

                if not os.path.isfile(our_config_file_path):
                    LOG.info("package '%s' config file '%s' not found"
                             " in script_dir: %s", pkg_name, config_file,
                             our_config_file_path)
                    continue
            else:
                # Their config file is outside script/plugin install dirs,
                # so no way user has it even installed, much less modified.
                LOG.warning("package '%s' config file '%s' not within"
                            " plugin_dir or script_dir", pkg_name, config_file)
                continue

            if not filecmp.cmp(our_config_file_path, their_config_file_path):
                rval.append((config_file, our_config_file_path))

        return rval

    def backup_modified_files(self, backup_subdir, modified_files):
        """Creates backups of modified config files

        Args:
            modified_files(list of (str, str)): the return value of
                :meth:`modified_config_files()`.

            backup_subdir(str): the subdir of `backup_dir` in which

        Returns:
            list of str: paths indicating the backup locations.  The order
            of the returned list corresponds directly to the order of
            `modified_files`.
        """
        import time
        rval = []

        for modified_file in modified_files:
            config_file = modified_file[0]
            config_file_dir = os.path.dirname(config_file)
            install_path = modified_file[1]
            filename = os.path.basename(install_path)
            backup_dir = os.path.join(
                self.backup_dir, backup_subdir, config_file_dir)
            timestamp = time.strftime('.%Y-%m-%d-%H:%M:%S')
            backup_path = os.path.join(backup_dir, filename + timestamp)
            make_dir(backup_dir)
            shutil.copy2(install_path, backup_path)
            rval.append(backup_path)

        return rval

    def refresh_source(self, name, aggregate=False, push=False):
        """Pull latest git information from a package source.

        This makes the latest pre-aggregated package metadata available or
        performs the aggregation locally in order to push it to the actual
        package source.  Locally aggregated data also takes precedence over
        the source's pre-aggregated data, so it can be useful in the case
        the operator of the source does not update their pre-aggregated data
        at a frequent enough interval.

        Args:
            name(str): the name of the package source.  E.g. the same name
                used as a key to :meth:`add_source()`.

            aggregate (bool): whether to perform a local metadata aggregation
                by crawling all packages listed in the source's index files.

            push (bool): whether to push local changes to the aggregated
                metadata to the remote package source.  If the `aggregate`
                flag is set, the data will be pushed after the aggregation
                is finished.

        Returns:
            str: an empty string if no errors occurred, else a description
            of what went wrong.
        """
        if name not in self.sources:
            return 'source name does not exist'

        source = self.sources[name]
        LOG.debug('refresh "%s": pulling %s', name, source.git_url)
        aggregate_file = os.path.join(source.clone.working_dir,
                                      AGGREGATE_DATA_FILE)
        agg_file_ours = os.path.join(
            self.scratch_dir, AGGREGATE_DATA_FILE)
        agg_file_their_orig = os.path.join(self.scratch_dir,
                                           AGGREGATE_DATA_FILE + '.orig')

        delete_path(agg_file_ours)
        delete_path(agg_file_their_orig)

        if os.path.isfile(aggregate_file):
            shutil.copy2(aggregate_file, agg_file_ours)

        source.clone.git.reset(hard=True)
        source.clone.git.clean('-f', '-x', '-d')

        if os.path.isfile(aggregate_file):
            shutil.copy2(aggregate_file, agg_file_their_orig)

        try:
            source.clone.git.fetch('--recurse-submodules=yes')
            source.clone.git.pull()
            source.clone.git.submodule('sync', '--recursive')
            source.clone.git.submodule('update', '--recursive', '--init')
        except git.exc.GitCommandError as error:
            LOG.error('failed to pull source %s: %s', name, error)
            return 'failed to pull from remote source: {}'.format(error)

        if os.path.isfile(agg_file_ours):
            if os.path.isfile(aggregate_file):
                # There's a tracked version of the file after pull.
                if os.path.isfile(agg_file_their_orig):
                    # We had local modifications to the file.
                    if filecmp.cmp(aggregate_file, agg_file_their_orig):
                        # Their file hasn't changed, use ours.
                        shutil.copy2(agg_file_ours, aggregate_file)
                        LOG.debug(
                            "aggegrate file in source unchanged, restore local one")
                    else:
                        # Their file changed, use theirs.
                        LOG.debug(
                            "aggegrate file in source changed, discard local one")
                else:
                    # File was untracked before pull and tracked after,
                    # use their version.
                    LOG.debug("new aggegrate file in source, discard local one")
            else:
                # They don't have the file after pulling, so restore ours.
                shutil.copy2(agg_file_ours, aggregate_file)
                LOG.debug("no aggegrate file in source, restore local one")

        if aggregate:
            # Use raw parser so no value interpolation takes place.
            parser = configparser.RawConfigParser()

            for index_file in source.package_index_files():
                urls = []

                with open(index_file) as f:
                    urls = [line.rstrip('\n') for line in f]

                for url in urls:
                    pkg_name = name_from_path(url)
                    clonepath = os.path.join(self.scratch_dir, pkg_name)
                    delete_path(clonepath)

                    try:
                        clone = git_clone(url, clonepath, shallow=True)
                    except git.exc.GitCommandError as error:
                        LOG.warn('failed to clone %s, skipping aggregation: %s',
                                 url, error)
                        continue

                    version_tags = _get_version_tags(clone)

                    if len(version_tags):
                        version = version_tags[-1]
                    else:
                        version = 'master'

                    git_checkout(clone, version)

                    metadata_file = _pick_metadata_file(clone.working_dir)
                    # Use raw parser so no value interpolation takes place.
                    metadata_parser = configparser.RawConfigParser()
                    invalid_reason = _parse_package_metadata(
                        metadata_parser, metadata_file)

                    if invalid_reason:
                        LOG.warn('skipping aggregation of %s: bad metadata: %s',
                                 url, invalid_reason)
                        continue

                    metadata = _get_package_metadata(metadata_parser)
                    index_dir = os.path.dirname(index_file)[len(
                        self.source_clonedir) + len(name) + 2:]
                    qualified_name = os.path.join(index_dir, pkg_name)

                    parser.add_section(qualified_name)

                    for key, value in sorted(metadata.items()):
                        parser.set(qualified_name, key, value)

                    parser.set(qualified_name, 'url', url)
                    parser.set(qualified_name, 'version', version)

            with open(aggregate_file, 'w') as f:
                parser.write(f)

        if push:
            if os.path.isfile(os.path.join(source.clone.working_dir,
                                           AGGREGATE_DATA_FILE)):
                source.clone.git.add(AGGREGATE_DATA_FILE)

            if source.clone.is_dirty():
                source.clone.git.commit(
                    '--message', 'Update aggregated metadata.')

            source.clone.git.push()

        return ''

    def refresh_installed_packages(self):
        """Fetch latest git information for installed packages.

        This retrieves information about outdated packages, but does
        not actually upgrade their installations.

        Raises:
            IOError: if the package manifest file can't be written
        """
        for ipkg in self.installed_packages():
            clonepath = os.path.join(self.package_clonedir, ipkg.package.name)
            clone = git.Repo(clonepath)
            LOG.debug('fetch package %s', ipkg.package.qualified_name())

            try:
                clone.git.fetch('--recurse-submodules=yes')
            except git.exc.GitCommandError as error:
                LOG.warn('failed to fetch package %s: %s',
                         ipkg.package.qualified_name(), error)

            ipkg.status.is_outdated = _is_clone_outdated(
                clone, ipkg.status.current_version, ipkg.status.tracking_method)

        self._write_manifest()

    def upgrade(self, pkg_path):
        """Upgrade a package to the latest available version.

        Args:
            pkg_path (str): the full git URL of a package or the shortened
                path/name that refers to it within a package source.  E.g. for
                a package source called "zeek" with package named "foo" in
                :file:`alice/zkg.index`, the following inputs may refer
                to the package: "foo", "alice/foo", or "zeek/alice/foo".

        Returns:
            str: an empty string if package upgrade succeeded else an error
            string explaining why it failed.

        Raises:
            IOError: if the manifest can't be written
        """
        pkg_path = canonical_url(pkg_path)
        LOG.debug('upgrading "%s"', pkg_path)
        ipkg = self.find_installed_package(pkg_path)

        if not ipkg:
            LOG.info('upgrading "%s": no matching package', pkg_path)
            return "no such package installed"

        if ipkg.status.is_pinned:
            LOG.info('upgrading "%s": package is pinned', pkg_path)
            return "package is pinned"

        if not ipkg.status.is_outdated:
            LOG.info('upgrading "%s": package not outdated', pkg_path)
            return "package is not outdated"

        clonepath = os.path.join(self.package_clonedir, ipkg.package.name)
        clone = git.Repo(clonepath)

        if ipkg.status.tracking_method == TRACKING_METHOD_VERSION:
            version_tags = _get_version_tags(clone)
            return self._install(ipkg.package, version_tags[-1])
        elif ipkg.status.tracking_method == TRACKING_METHOD_BRANCH:
            clone.git.pull()
            clone.git.submodule('sync', '--recursive')
            clone.git.submodule('update', '--recursive', '--init')
            return self._install(ipkg.package, ipkg.status.current_version)
        elif ipkg.status.tracking_method == TRACKING_METHOD_COMMIT:
            # The above check for whether the installed package is outdated
            # also should have already caught this situation.
            return "package is not outdated"
        else:
            raise NotImplementedError

    def remove(self, pkg_path):
        """Remove an installed package.

        Args:
            pkg_path (str): the full git URL of a package or the shortened
                path/name that refers to it within a package source.  E.g. for
                a package source called "zeek" with package named "foo" in
                :file:`alice/zkg.index`, the following inputs may refer
                to the package: "foo", "alice/foo", or "zeek/alice/foo".

        Returns:
            bool: True if an installed package was removed, else False.

        Raises:
            IOError: if the package manifest file can't be written
            OSError: if the installed package's directory can't be deleted
        """

        pkg_path = canonical_url(pkg_path)
        LOG.debug('removing "%s"', pkg_path)
        ipkg = self.find_installed_package(pkg_path)

        if not ipkg:
            LOG.info('removing "%s": could not find matching package', pkg_path)
            return False

        self.unload(pkg_path)

        pkg_to_remove = ipkg.package
        delete_path(os.path.join(self.package_clonedir, pkg_to_remove.name))
        delete_path(os.path.join(self.script_dir, pkg_to_remove.name))
        delete_path(os.path.join(self.plugin_dir, pkg_to_remove.name))
        delete_path(os.path.join(self.zeekpath(), pkg_to_remove.name))

        for alias in pkg_to_remove.aliases():
            delete_path(os.path.join(self.zeekpath(), alias))

        del self.installed_pkgs[pkg_to_remove.name]
        self._write_manifest()

        LOG.debug('removed "%s"', pkg_path)
        return True

    def pin(self, pkg_path):
        """Pin a currently installed package to the currently installed version.

        Pinned packages are never upgraded when calling :meth:`upgrade()`.

        Args:
            pkg_path (str): the full git URL of a package or the shortened
                path/name that refers to it within a package source.  E.g. for
                a package source called "zeek" with package named "foo" in
                :file:`alice/zkg.index`, the following inputs may refer
                to the package: "foo", "alice/foo", or "zeek/alice/foo".

        Returns:
            :class:`.package.InstalledPackage`: None if no matching installed
            package could be found, else the installed package that was pinned.

        Raises:
            IOError: when the manifest file can't be written
        """
        pkg_path = canonical_url(pkg_path)
        LOG.debug('pinning "%s"', pkg_path)
        ipkg = self.find_installed_package(pkg_path)

        if not ipkg:
            LOG.info('pinning "%s": no matching package', pkg_path)
            return None

        if ipkg.status.is_pinned:
            LOG.debug('pinning "%s": already pinned', pkg_path)
            return ipkg

        ipkg.status.is_pinned = True
        self._write_manifest()
        LOG.debug('pinned "%s"', pkg_path)
        return ipkg

    def unpin(self, pkg_path):
        """Unpin a currently installed package and allow it to be upgraded.

        Args:
            pkg_path (str): the full git URL of a package or the shortened
                path/name that refers to it within a package source.  E.g. for
                a package source called "zeek" with package named "foo" in
                :file:`alice/zkg.index`, the following inputs may refer
                to the package: "foo", "alice/foo", or "zeek/alice/foo".

        Returns:
            :class:`.package.InstalledPackage`: None if no matching installed
            package could be found, else the installed package that was unpinned.

        Raises:
            IOError: when the manifest file can't be written
        """
        pkg_path = canonical_url(pkg_path)
        LOG.debug('unpinning "%s"', pkg_path)
        ipkg = self.find_installed_package(pkg_path)

        if not ipkg:
            LOG.info('unpinning "%s": no matching package', pkg_path)
            return None

        if not ipkg.status.is_pinned:
            LOG.debug('unpinning "%s": already unpinned', pkg_path)
            return ipkg

        ipkg.status.is_pinned = False
        self._write_manifest()
        LOG.debug('unpinned "%s"', pkg_path)
        return ipkg

    def load(self, pkg_path):
        """Mark an installed package as being "loaded".

        The collection of "loaded" packages is a convenient way for Zeek to more
        simply load a whole group of packages installed via the package manager.

        Args:
            pkg_path (str): the full git URL of a package or the shortened
                path/name that refers to it within a package source.  E.g. for
                a package source called "zeek" with package named "foo" in
                :file:`alice/zkg.index`, the following inputs may refer
                to the package: "foo", "alice/foo", or "zeek/alice/foo".

        Returns:
            str: empty string if the package is successfully marked as loaded,
            else an explanation of why it failed.

        Raises:
            IOError: if the loader script or manifest can't be written
        """
        pkg_path = canonical_url(pkg_path)
        LOG.debug('loading "%s"', pkg_path)
        ipkg = self.find_installed_package(pkg_path)

        if not ipkg:
            LOG.info('loading "%s": no matching package', pkg_path)
            return 'no such package'

        if ipkg.status.is_loaded:
            LOG.debug('loading "%s": already loaded', pkg_path)
            return ''

        pkg_load_script = os.path.join(self.script_dir, ipkg.package.name,
                                       '__load__.zeek')
        # Check if __load__.bro exists for compatibility with older packages
        pkg_load_fallback = os.path.join(self.script_dir, ipkg.package.name,
                                       '__load__.bro')

        if (not os.path.exists(pkg_load_script) and
            not os.path.exists(pkg_load_fallback) and
            not self.has_plugin(ipkg)):
            LOG.debug('loading "%s": %s not found and package has no plugin',
                      pkg_path, pkg_load_script)
            return 'no __load__.zeek within package script_dir and no plugin included'

        ipkg.status.is_loaded = True
        self._write_autoloader()
        self._write_manifest()
        self._write_plugin_magic(ipkg)
        LOG.debug('loaded "%s"', pkg_path)
        return ''

    def loaded_package_states(self):
        """Save "loaded" state for all installed packages.

        Returns:
            dict: dictionary of "loaded" status for installed packages
        """
        return {name: ipkg.status.is_loaded for name, ipkg in self.installed_pkgs.items()}

    def restore_loaded_package_states(self, saved_state):
        """Restores state for installed packages.

        Args:
            saved_state (dict): dictionary of saved "loaded" state for installed
            packages.

        """
        for pkg_name, ipkg in self.installed_pkgs.items():
            if ipkg.status.is_loaded == saved_state[pkg_name]:
                continue

            ipkg.status.is_loaded = saved_state[pkg_name]
            self._write_plugin_magic(ipkg)

        self._write_autoloader()
        self._write_manifest()

    def load_with_dependencies(self, pkg_name, visited=set()):
        """Mark dependent (but previously installed) packages as being "loaded".

        Args:
            pkg_name (str): name of the package.

            visited (set(str)): set of packages visited along the recursive loading

        Returns:
            list(str, str): list of tuples containing dependent package name and whether
            it was marked as loaded or else an explanation of why the loading failed.

        """
        ipkg = self.find_installed_package(pkg_name)

        # skip loading a package if it is not installed.
        if not ipkg:
            return [(pkg_name, 'Loading dependency failed. Package not installed.')]

        load_error = self.load(pkg_name)

        if load_error:
            return [(pkg_name, load_error)]

        retval = []
        visited.add(pkg_name)

        for pkg in self.get_installed_package_dependencies(pkg_name):
            if pkg in visited:
                continue

            retval += self.load_with_dependencies(pkg, visited)

        return retval

    def list_depender_pkgs(self, pkg_path):
        """List of depender packages packages.

        If C depends on B and B depends on A, we represent the dependency
        chain as C -> B -> A. Thus, package C is dependent on A and B,
        while package B is dependent on just C. So pkg_dependencies would
        be::

            {
            'A': set(),
            'B': set([A, version_of_A])
            'C': set([B, version_of_B])
            }

        Further, package A is a direct dependee for B (and implicitly for C),
        while B is a direct depender (and C is an implicit depender) for A.

        Args:
            pkg_path (str): the full git URL of a package or the shortened
                path/name that refers to it within a package source.  E.g. for
                a package source called "zeek" with package named "foo" in
                :file:`alice/zkg.index`, the following inputs may refer
                to the package: "foo", "alice/foo", or "zeek/alice/foo".

        Returns:
            list: list of depender packages.
        """
        depender_packages, pkg_name = set(), name_from_path(pkg_path)
        queue = deque([pkg_name])
        pkg_dependencies = self.installed_package_dependencies()

        while queue:
            item = queue.popleft()

            for _pkg_name in pkg_dependencies:
                pkg_dependees = set([_pkg for _pkg in pkg_dependencies.get(_pkg_name)])

                if item in pkg_dependees:
                    # check if there is a cyclic dependency
                    if _pkg_name == pkg_name:
                        return sorted([pkg for pkg in depender_packages] + [pkg_name])

                    queue.append(_pkg_name)
                    depender_packages.add(_pkg_name)

        return sorted([pkg for pkg in depender_packages])

    def unload_with_unused_dependers(self, pkg_name):
        """Unmark dependent (but previously installed packages) as being "loaded".

        Args:
            pkg_name (str): name of the package.

        Returns:
            list(str, str): list of tuples containing dependent package name and
            whether it was marked as unloaded or else an explanation of why the
            unloading failed.

        Raises:
            IOError: if the loader script or manifest can't be written
        """
        def _has_all_dependers_unloaded(item, dependers):
            for depender in dependers:
                ipkg = self.find_installed_package(depender)
                if ipkg and ipkg.status.is_loaded:
                    return False
            return True

        errors = []
        queue = deque([pkg_name])

        while queue:
            item = queue.popleft()
            deps = self.get_installed_package_dependencies(item)

            for pkg in deps:
                ipkg = self.find_installed_package(pkg)
                # it is possible that this dependency has been removed via zkg

                if not ipkg:
                    errors.append((pkg, 'Package not installed.'))
                    return errors

                if ipkg.status.is_loaded:
                    queue.append(pkg)

            ipkg = self.find_installed_package(item)

            # it is possible that this package has been removed via zkg
            if not ipkg:
                errors.append((item, 'Package not installed.'))
                return errors

            if ipkg.status.is_loaded:
                dep_packages = self.list_depender_pkgs(item)

                # check if there is a cyclic dependency
                if item in dep_packages:
                    for dep in dep_packages:
                        if item != dep:
                            ipkg = self.find_installed_package(dep)

                            if ipkg and ipkg.status.is_loaded:
                                self.unload(dep)
                                errors.append((dep, ''))

                    self.unload(item)
                    errors.append((item, ''))
                    continue

                # check if all dependers are unloaded
                elif _has_all_dependers_unloaded(item, dep_packages):
                    self.unload(item)
                    errors.append((item, ''))
                    continue

                # package is in use
                else:
                    dep_packages = self.list_depender_pkgs(pkg_name)
                    dep_listing = ''

                    for _name in dep_packages:
                        dep_listing += '"{}", '.format(_name)

                    errors.append((item, 'Package is in use by other packages --- {}.'.format(dep_listing[:-2])))
                    return errors

        return errors

    def unload(self, pkg_path):
        """Unmark an installed package as being "loaded".

        The collection of "loaded" packages is a convenient way for Zeek to more
        simply load a whole group of packages installed via the package manager.

        Args:
            pkg_path (str): the full git URL of a package or the shortened
                path/name that refers to it within a package source.  E.g. for
                a package source called "zeek" with package named "foo" in
                :file:`alice/zkg.index`, the following inputs may refer
                to the package: "foo", "alice/foo", or "zeek/alice/foo".

        Returns:
            bool: True if a package is successfully unmarked as loaded.

        Raises:
            IOError: if the loader script or manifest can't be written
        """
        pkg_path = canonical_url(pkg_path)
        LOG.debug('unloading "%s"', pkg_path)
        ipkg = self.find_installed_package(pkg_path)

        if not ipkg:
            LOG.info('unloading "%s": no matching package', pkg_path)
            return False

        if not ipkg.status.is_loaded:
            LOG.debug('unloading "%s": already unloaded', pkg_path)
            return True

        ipkg.status.is_loaded = False
        self._write_autoloader()
        self._write_manifest()
        self._write_plugin_magic(ipkg)
        LOG.debug('unloaded "%s"', pkg_path)
        return True

    def bundle_info(self, bundle_file):
        """Retrieves information on all packages contained in a bundle.

        Args:
            bundle_file (str): the path to the bundle to inspect.

        Returns:
            (str, list of (str, str, :class:`.package.PackageInfo`)): a tuple
            with the the first element set to an empty string if the information
            successfully retrieved, else an error message explaining why the
            bundle file was invalid.  The second element of the tuple is a list
            containing information on each package contained in the bundle:
            the exact git URL and version string from the bundle's manifest
            along with the package info object retrieved by inspecting git repo
            contained in the bundle.
        """
        LOG.debug('getting bundle info for file "%s"', bundle_file)
        bundle_dir = os.path.join(self.scratch_dir, 'bundle')
        delete_path(bundle_dir)
        make_dir(bundle_dir)
        infos = []

        try:
            with tarfile.open(bundle_file) as tf:
                tf.extractall(bundle_dir)
        except Exception as error:
            return (str(error), infos)

        manifest_file = os.path.join(bundle_dir, 'manifest.txt')
        config = GoodConfigParser(delimiters='=')
        config.optionxform = str

        if not config.read(manifest_file):
            return ('invalid bundle: no manifest file', infos)

        if not config.has_section('bundle'):
            return ('invalid bundle: no [bundle] section in manifest file',
                    infos)

        manifest = config.items('bundle')

        for git_url, version in manifest:
            package = Package(git_url=git_url, name=git_url.split('/')[-1],
                              canonical=True)
            pkg_path = os.path.join(bundle_dir, package.name)
            LOG.debug('getting info for bundled package "%s"', package.name)
            pkg_info = self.info(pkg_path, version=version,
                                 prefer_installed=False)
            infos.append((git_url, version, pkg_info))

        return ('', infos)

    def info(self, pkg_path, version='', prefer_installed=True):
        """Retrieves information about a package.

        Args:
            pkg_path (str): the full git URL of a package or the shortened
                path/name that refers to it within a package source.  E.g. for
                a package source called "zeek" with package named "foo" in
                :file:`alice/zkg.index`, the following inputs may refer
                to the package: "foo", "alice/foo", or "zeek/alice/foo".

            version (str): may be a git version tag, branch name, or commit hash
                from which metadata will be pulled.  If an empty string is
                given, then the latest git version tag is used (or the "master"
                branch if no version tags exist).

            prefer_installed (bool): if this is set, then the information from
                any current installation of the package is returned instead of
                retrieving the latest information from the package's git repo.
                The `version` parameter is also ignored when this is set as
                it uses whatever version of the package is currently installed.

        Returns:
            A :class:`.package.PackageInfo` object.
        """
        pkg_path = canonical_url(pkg_path)
        name = name_from_path(pkg_path)

        if not is_valid_package_name(name):
            reason = 'Package name {!r} is not valid.'.format(name, pkg_path)
            return PackageInfo(Package(git_url=pkg_path), invalid_reason=reason)

        LOG.debug('getting info on "%s"', pkg_path)
        ipkg = self.find_installed_package(pkg_path)

        if prefer_installed and ipkg:
            status = ipkg.status
            pkg_name = ipkg.package.name
            clonepath = os.path.join(self.package_clonedir, pkg_name)
            clone = git.Repo(clonepath)
            return _info_from_clone(clone, ipkg.package, status,
                                    status.current_version)
        else:
            status = None
            matches = self.match_source_packages(pkg_path)

        if not matches:
            package = Package(git_url=pkg_path)

            try:
                return self._info(package, status, version)
            except git.exc.GitCommandError as error:
                LOG.info('getting info on "%s": invalid git repo path: %s',
                         pkg_path, error)

            LOG.info('getting info on "%s": matched no source package',
                     pkg_path)
            reason = ('package name not found in sources and also'
                      ' not a usable git URL (invalid or inaccessible,'
                      ' use -vvv for details)')
            return PackageInfo(package=package, invalid_reason=reason,
                               status=status)

        if len(matches) > 1:
            matches_string = [match.qualified_name() for match in matches]
            LOG.info('getting info on "%s": matched multiple packages: %s',
                     pkg_path, matches_string)
            reason = str.format('"{}" matches multiple packages, try a more'
                                ' specific name from: {}',
                                pkg_path, matches_string)
            return PackageInfo(invalid_reason=reason, status=status)

        package = matches[0]

        try:
            return self._info(package, status, version)
        except git.exc.GitCommandError as error:
            LOG.info('getting info on "%s": invalid git repo path: %s',
                     pkg_path, error)
            reason = 'git repository is either invalid or unreachable'
            return PackageInfo(package=package, invalid_reason=reason,
                               status=status)

    def _info(self, package, status, version):
        """Retrieves information about a package.

        Returns:
            A :class:`.package.PackageInfo` object.

        Raises:
            git.exc.GitCommandError: when failing to clone the package repo
        """
        clonepath = os.path.join(self.scratch_dir, package.name)
        clone = _clone_package(package, clonepath, version)
        versions = _get_version_tags(clone)

        if not version:

            if len(versions):
                version = versions[-1]
            else:
                if 'master' not in _get_branch_names(clone):
                    reason = 'git repo has no "master" branch or version tags'
                    return PackageInfo(package=package, status=status,
                                       invalid_reason=reason)

                version = 'master'

        try:
            git_checkout(clone, version)
        except git.exc.GitCommandError:
            reason = 'no such commit, branch, or version tag: "{}"'.format(
                version)
            return PackageInfo(package=package, status=status,
                               invalid_reason=reason)

        LOG.debug('checked out "%s", branch/version "%s"', package, version)
        return _info_from_clone(clone, package, status, version)

    def package_versions(self, installed_package):
        """Returns a list of version number tags available for a package.

        Args:
            installed_package (:class:`.package.InstalledPackage`): the package
                for which version number tags will be retrieved.

        Returns:
            list of str: the version number tags.
        """
        name = installed_package.package.name
        clonepath = os.path.join(self.package_clonedir, name)
        clone = git.Repo(clonepath)
        return _get_version_tags(clone)

    def validate_dependencies(self, requested_packages,
                              ignore_installed_packages=False,
                              ignore_suggestions=False):
        """Validates package dependencies.

        Args:
            requested_packages (list of (str, str)): a list of (package name or
                git URL, version) string tuples validate.  If the version string
                is empty, the latest available version of the package is used.


            ignore_installed_packages (bool): whether the dependency analysis
                should consider installed packages as satisfying dependency
                requirements.

            ignore_suggestions (bool): whether the dependency analysis should
                consider installing dependencies that are marked in another
                package's 'suggests' metadata field.

        Returns:
            (str, list of (:class:`.package.PackageInfo`, str, bool)):
            the first element of the tuple is an empty string if dependency
            graph was successfully validated, else an error string explaining
            what is invalid.  In the case it was validated, the second element
            is a list of tuples where the first elements are dependency packages
            that would need to be installed in order to satisfy the
            dependencies of the requested packages (it will not include any
            packages that are already installed or that are in the
            `requested_packages` argument).
            The second element of tuples in the list is a version string of
            the associated package that satisfies dependency requirements.
            The third element of the tuples in the list is a boolean value
            indicating whether the package is included in the list because it's
            merely suggested by another package.

        """
        class Node(object):

            def __init__(self, name):
                self.name = name
                self.info = None
                self.requested_version = None  # (tracking method, version)
                self.installed_version = None  # (tracking method, version)
                self.dependers = dict()  # name -> version
                self.is_suggestion = False

            def __str__(self):
                return str.format(
                    '{}\n\trequested: {}\n\tinstalled: {}\n\tdependers: {}\n\tsuggestion: {}',
                    self.name, self.requested_version, self.installed_version,
                    self.dependers, self.is_suggestion)

        new_pkgs = []
        graph = dict()

        # 1. Try to make nodes for everything in the dependency graph...

        # Add nodes for packages that are requested for installation
        for name, version in requested_packages:
            info = self.info(name, version=version, prefer_installed=False)

            if info.invalid_reason:
                return ('invalid package "{}": {}'.format(name,
                                                          info.invalid_reason),
                        new_pkgs)

            node = Node(info.package.qualified_name())
            node.info = info
            method = node.info.version_type
            node.requested_version = (method, version)
            graph[node.name] = node

        # Recursively add nodes for all dependencies of requested packages
        to_process = copy.copy(graph)

        while to_process:
            (_, node) = to_process.popitem()
            dd = node.info.dependencies(field='depends')
            ds = node.info.dependencies(field='suggests')

            if dd is None:
                return (str.format('package "{}" has malformed "depends" field',
                                   node.name), new_pkgs)

            all_deps = dd.copy()

            if not ignore_suggestions:
                if ds is None:
                    return (str.format('package "{}" has malformed "suggests" field',
                                       node.name), new_pkgs)

                all_deps.update(ds)

            for dep_name, _ in all_deps.items():
                if dep_name == 'bro' or dep_name == 'zeek':
                    # A zeek node will get added later.
                    continue

                if dep_name == 'bro-pkg' or dep_name == 'zkg':
                    # A zkg node will get added later.
                    continue

                # Suggestion status propagates to 'depends' field of suggested packages.
                is_suggestion = node.is_suggestion or dep_name in ds and dep_name not in dd
                info = self.info(dep_name, prefer_installed=False)

                if info.invalid_reason:
                    return (str.format(
                        'package "{}" has invalid dependency "{}": {}',
                        node.name, dep_name, info.invalid_reason), new_pkgs)

                dep_name = info.package.qualified_name()

                if dep_name in graph:
                    if graph[dep_name].is_suggestion and not is_suggestion:
                        # Suggestion found to be required by another package.
                        graph[dep_name].is_suggestion = False
                    continue

                if dep_name in to_process:
                    if to_process[dep_name].is_suggestion and not is_suggestion:
                        # Suggestion found to be required by another package.
                        to_process[dep_name].is_suggestion = False
                    continue

                node = Node(dep_name)
                node.info = info
                node.is_suggestion = is_suggestion
                graph[node.name] = node
                to_process[node.name] = node

        # Add nodes for things that are already installed (including zeek)
        if not ignore_installed_packages:
            zeek_version = get_zeek_version()

            if zeek_version:
                node = Node('zeek')
                node.installed_version = (TRACKING_METHOD_VERSION, zeek_version)
                graph['zeek'] = node
            else:
                LOG.warning(
                    'could not get zeek version: no "zeek-config" or "bro-config" in PATH ?')

            node = Node('zkg')
            node.installed_version = (TRACKING_METHOD_VERSION, __version__)
            graph['zkg'] = node

            for ipkg in self.installed_packages():
                name = ipkg.package.qualified_name()
                status = ipkg.status

                if name not in graph:
                    info = self.info(name, prefer_installed=True)
                    node = Node(name)
                    node.info = info
                    graph[node.name] = node

                graph[name].installed_version = (
                    status.tracking_method, status.current_version)

        # 2. Fill in the edges of the graph with dependency information.
        for name, node in graph.items():
            if name == 'zeek':
                continue

            if name == 'zkg':
                continue

            dd = node.info.dependencies(field='depends')
            ds = node.info.dependencies(field='suggests')

            if dd is None:
                return (str.format('package "{}" has malformed "depends" field',
                                   node.name), new_pkgs)

            all_deps = dd.copy()

            if not ignore_suggestions:
                if ds is None:
                    return (str.format('package "{}" has malformed "suggests" field',
                                       node.name), new_pkgs)

                all_deps.update(ds)

            for dep_name, dep_version in all_deps.items():
                if dep_name == 'bro' or dep_name == 'zeek':
                    if 'zeek' in graph:
                        graph['zeek'].dependers[name] = dep_version
                elif dep_name == 'bro-pkg' or dep_name == 'zkg':
                    if 'zkg' in graph:
                        graph['zkg'].dependers[name] = dep_version
                else:
                    for _, dependency_node in graph.items():
                        if dependency_node.name == 'zeek':
                            continue

                        if dependency_node.name == 'zkg':
                            continue

                        if dependency_node.info.package.matches_path(dep_name):
                            dependency_node.dependers[name] = dep_version
                            break

        # 3. Try to solve for a connected graph with no edge conflicts.
        for name, node in graph.items():
            if not node.dependers:
                if node.installed_version:
                    continue

                if node.requested_version:
                    continue

                new_pkgs.append((node.info, node.info.best_version(),
                                 node.is_suggestion))
                continue

            if node.requested_version:
                # Check that requested version doesn't conflict with dependers.
                track_method, required_version = node.requested_version

                if track_method == TRACKING_METHOD_BRANCH:
                    for depender_name, version_spec in node.dependers.items():
                        if version_spec == '*':
                            continue

                        if version_spec.startswith('branch='):
                            version_spec = version_spec[len('branch='):]

                        if version_spec == required_version:
                            continue

                        return (str.format(
                            'unsatisfiable dependency: requested "{}" ({}),'
                            ' but "{}" requires {}', node.name,
                            required_version, depender_name, version_spec),
                            new_pkgs)
                elif track_method == TRACKING_METHOD_COMMIT:
                    for depender_name, version_spec in node.dependers.items():
                        if version_spec == '*':
                            continue

                        # Could allow commit= version specification like what
                        # is done with branches, but unsure there's a common
                        # use-case for it.

                        return (str.format(
                            'unsatisfiable dependency: requested "{}" ({}),'
                            ' but "{}" requires {}', node.name,
                            required_version, depender_name, version_spec),
                            new_pkgs)
                else:
                    normal_version = _normalize_version_tag(required_version)
                    req_semver = semver.Version.coerce(normal_version)

                    for depender_name, version_spec in node.dependers.items():
                        if version_spec.startswith('branch='):
                            version_spec = version_spec[len('branch='):]
                            return (str.format(
                                'unsatisfiable dependency: requested "{}" ({}),'
                                ' but "{}" requires {}', node.name,
                                required_version, depender_name, version_spec),
                                new_pkgs)
                        else:
                            try:
                                semver_spec = semver.Spec(version_spec)
                            except ValueError:
                                return (str.format(
                                    'package "{}" has invalid semver spec: {}',
                                    depender_name, version_spec), new_pkgs)

                            if req_semver not in semver_spec:
                                return (str.format(
                                    'unsatisfiable dependency: requested "{}" ({}),'
                                    ' but "{}" requires {}', node.name,
                                    required_version, depender_name, version_spec),
                                    new_pkgs)
            elif node.installed_version:
                # Check that installed version doesn't conflict with dependers.
                track_method, required_version = node.installed_version

                for depender_name, version_spec in node.dependers.items():
                    if ( depender_name in graph and
                         graph[depender_name].installed_version ):
                        # Both nodes already installed, so assume compatible.
                        # They're maybe not actually if the user installed via
                        # --nodeps, but if that's their desired state, it's
                        # better not to complain about it now.
                        continue

                    if track_method == TRACKING_METHOD_BRANCH:
                        if version_spec == '*':
                            continue

                        if version_spec.startswith('branch='):
                            version_spec = version_spec[len('branch='):]

                        if version_spec == required_version:
                            continue

                        return (str.format(
                            'unsatisfiable dependency: "{}" ({}) is installed,'
                            ' but "{}" requires {}', node.name,
                            required_version, depender_name, version_spec),
                            new_pkgs)
                    elif track_method == TRACKING_METHOD_COMMIT:
                        if version_spec == '*':
                            continue

                        # Could allow commit= version specification like what
                        # is done with branches, but unsure there's a common
                        # use-case for it.

                        return (str.format(
                            'unsatisfiable dependency: "{}" ({}) is installed,'
                            ' but "{}" requires {}', node.name,
                            required_version, depender_name, version_spec),
                            new_pkgs)
                    else:
                        normal_version = _normalize_version_tag(required_version)
                        req_semver = semver.Version.coerce(normal_version)

                        if version_spec.startswith('branch='):
                            version_spec = version_spec[len('branch='):]
                            return (str.format(
                                'unsatisfiable dependency: "{}" ({}) is installed,'
                                ' but "{}" requires {}', node.name,
                                required_version, depender_name, version_spec),
                                new_pkgs)
                        else:
                            try:
                                semver_spec = semver.Spec(version_spec)
                            except ValueError:
                                return (str.format(
                                    'package "{}" has invalid semver spec: {}',
                                    depender_name, version_spec), new_pkgs)

                            if req_semver not in semver_spec:
                                return (str.format(
                                    'unsatisfiable dependency: "{}" ({}) is installed,'
                                    ' but "{}" requires {}', node.name,
                                    required_version, depender_name, version_spec),
                                    new_pkgs)
            else:
                # Choose best version that satisfies constraints
                if not node.info.versions:
                    best_version = 'master'

                    for depender_name, version_spec in node.dependers.items():
                        if version_spec == '*':
                            continue

                        if version_spec.startswith('branch='):
                            version_spec = version_spec[len('branch='):]

                        if version_spec == best_version:
                            continue

                        return (str.format(
                            'unsatisfiable dependency "{}": "{}" requires {}',
                            node.name, depender_name, version_spec), new_pkgs)
                else:
                    best_version = None
                    need_branch = False
                    need_version = False

                    def no_best_version_string(node):
                        rval = str.format(
                            '"{}" has no version satisfying dependencies:\n',
                            node.name)

                        for depender_name, version_spec in node.dependers.items():
                            rval += str.format('\t{} needs {}\n',
                                               depender_name, version_spec)

                        return (rval, new_pkgs)

                    for _, version_spec in node.dependers.items():
                        if version_spec.startswith('branch='):
                            need_branch = True
                        elif version_spec != '*':
                            need_version = True

                    if need_branch and need_version:
                        return (no_best_version_string(node), new_pkgs)

                    if need_branch:
                        branch_name = None

                        for depender_name, version_spec in node.dependers.items():
                            if version_spec == '*':
                                continue

                            if not branch_name:
                                branch_name = version_spec[len('branch='):]
                                continue

                            if branch_name != version_spec[len('branch='):]:
                                return (no_best_version_string(), new_pkgs)

                        if branch_name:
                            best_version = branch_name
                        else:
                            best_version = 'master'
                    elif need_version:
                        for version in node.info.versions[::-1]:
                            normal_version = _normalize_version_tag(version)
                            req_semver = semver.Version.coerce(normal_version)

                            satisfied = True

                            for depender_name, version_spec in node.dependers.items():
                                try:
                                    semver_spec = semver.Spec(version_spec)
                                except ValueError:
                                    return (str.format(
                                        'package "{}" has invalid semver spec: {}',
                                        depender_name, version_spec), new_pkgs)

                                if req_semver not in semver_spec:
                                    satisfied = False
                                    break

                            if satisfied:
                                best_version = version
                                break

                        if not best_version:
                            return (no_best_version_string(node), new_pkgs)
                    else:
                        # Must have been all '*' wildcards
                        best_version = node.info.best_version()

                new_pkgs.append((node.info, best_version, node.is_suggestion))

        return ('', new_pkgs)

    def bundle(self, bundle_file, package_list, prefer_existing_clones=False):
        """Creates a package bundle.

        Args:
            bundle_file (str): filesystem path of the zip file to create.

            package_list (list of (str, str)): a list of (git URL, version)
                string tuples to put in the bundle.  If the version string is
                empty, the latest available version of the package is used.

            prefer_existing_clones (bool): if True and the package list contains
                a package at a version that is already installed, then the
                existing git clone of that package is put into the bundle
                instead of cloning from the remote repository.

        Returns:
            str: empty string if the bundle is successfully created,
            else an error string explaining what failed.
        """
        bundle_dir = os.path.join(self.scratch_dir, 'bundle')
        delete_path(bundle_dir)
        make_dir(bundle_dir)
        manifest_file = os.path.join(bundle_dir, 'manifest.txt')
        config = GoodConfigParser(delimiters='=')
        config.optionxform = str
        config.add_section('bundle')

        def match_package_url_and_version(git_url, version):
            for ipkg in self.installed_packages():
                if ipkg.package.git_url != git_url:
                    continue

                if ipkg.status.current_version != version:
                    continue

                return ipkg

            return None

        for git_url, version in package_list:
            name = name_from_path(git_url)
            clonepath = os.path.join(bundle_dir, name)
            config.set('bundle', git_url, version)

            if prefer_existing_clones:
                ipkg = match_package_url_and_version(git_url, version)

                if ipkg:
                    src = os.path.join(
                        self.package_clonedir, ipkg.package.name)
                    shutil.copytree(src, clonepath, symlinks=True)
                    clone = git.Repo(clonepath)
                    clone.git.reset(hard=True)
                    clone.git.clean('-f', '-x', '-d')

                    for modified_config in self.modified_config_files(ipkg):
                        dst = os.path.join(clonepath, modified_config[0])
                        shutil.copy2(modified_config[1], dst)

                    continue

            try:
                git_clone(git_url, clonepath, shallow=(not is_sha1(version)))
            except git.exc.GitCommandError as error:
                return 'failed to clone {}: {}'.format(git_url, error)

        with open(manifest_file, 'w') as f:
            config.write(f)

        archive = shutil.make_archive(bundle_dir, 'gztar', bundle_dir)
        delete_path(bundle_file)
        shutil.move(archive, bundle_file)
        return ''

    def unbundle(self, bundle_file):
        """Installs all packages contained within a bundle.

        Args:
            bundle_file (str): the path to the bundle to install.

        Returns:
            str: an empty string if the operation was successful, else an error
            message indicated what went wrong.
        """
        LOG.debug('unbundle "%s"', bundle_file)
        bundle_dir = os.path.join(self.scratch_dir, 'bundle')
        delete_path(bundle_dir)
        make_dir(bundle_dir)

        try:
            with tarfile.open(bundle_file) as tf:
                tf.extractall(bundle_dir)
        except Exception as error:
            return str(error)

        manifest_file = os.path.join(bundle_dir, 'manifest.txt')
        config = GoodConfigParser(delimiters='=')
        config.optionxform = str

        if not config.read(manifest_file):
            return 'invalid bundle: no manifest file'

        if not config.has_section('bundle'):
            return 'invalid bundle: no [bundle] section in manifest file'

        manifest = config.items('bundle')

        for git_url, version in manifest:
            package = Package(git_url=git_url, name=git_url.split('/')[-1],
                              canonical=True)
            clonepath = os.path.join(self.package_clonedir, package.name)
            delete_path(clonepath)
            shutil.move(os.path.join(bundle_dir, package.name), clonepath)

            LOG.debug('unbundle installing "%s"', package.name)
            error = self._install(package, version, use_existing_clone=True)

            if error:
                return error

        return ''

    def test(self, pkg_path, version=''):
        """Test a package.

        Args:
            pkg_path (str): the full git URL of a package or the shortened
                path/name that refers to it within a package source.  E.g. for
                a package source called "zeek" with package named "foo" in
                :file:`alice/zkg.index`, the following inputs may refer
                to the package: "foo", "alice/foo", or "zeek/alice/foo".

            version (str): if not given, then the latest git version tag is
                used (or if no version tags exist, the "master" branch is
                used).  If given, it may be either a git version tag or a
                git branch name.

        Returns:
            (str, bool, str): a tuple containing an error message string,
            a boolean indicating whether the tests passed, as well as a path
            to the directory in which the tests were run.  In the case
            where tests failed, the directory can be inspected to figure out
            what went wrong.  In the case where the error message string is
            not empty, the error message indicates the reason why tests could
            not be run.
        """
        pkg_path = canonical_url(pkg_path)
        LOG.debug('testing "%s"', pkg_path)
        pkg_info = self.info(pkg_path, version=version, prefer_installed=False)

        if pkg_info.invalid_reason:
            return (pkg_info.invalid_reason, 'False', '')

        if 'test_command' not in pkg_info.metadata:
            return ('Package does not specify a test_command', False, '')

        if not version:
            version = pkg_info.metadata_version

        package = pkg_info.package
        test_dir = os.path.join(self.package_testdir, package.name)
        clone_dir = os.path.join(test_dir, 'clones')
        stage_script_dir = os.path.join(test_dir, 'scripts', 'packages')
        stage_plugin_dir = os.path.join(test_dir, 'plugins', 'packages')
        delete_path(test_dir)
        make_dir(clone_dir)
        make_dir(stage_script_dir)
        make_dir(stage_plugin_dir)

        request = [(package.qualified_name(), version)]
        invalid_deps, new_pkgs = self.validate_dependencies(request, True)

        if invalid_deps:
            return (invalid_deps, False, test_dir)

        pkgs = []
        pkgs.append((pkg_info, version))

        for info, version, _ in new_pkgs:
            pkgs.append((info, version))

        # Clone all packages, checkout right version, and build/install to
        # staging area.
        for info, version in pkgs:
            clonepath = os.path.join(clone_dir, info.package.name)

            try:
                clone = _clone_package(info.package, clonepath, version)
            except git.exc.GitCommandError as error:
                LOG.warning('failed to clone git repo: %s', error)
                return ('failed to clone {}'.format(info.package.git_url),
                        False, test_dir)

            try:
                git_checkout(clone, version)
            except git.exc.GitCommandError as error:
                LOG.warning('failed to checkout git repo version: %s', error)
                return (str.format('failed to checkout {} of {}',
                                   version, info.package.git_url),
                        False, test_dir)

            fail_msg = self._stage(info.package, version,
                                   clone, stage_script_dir, stage_plugin_dir)

            if fail_msg:
                return (fail_msg, False, test_dir)

        # Finally, run tests (with correct environment set)
        test_command = pkg_info.metadata['test_command']

        zeek_config = find_program('zeek-config')
        path_option = '--zeekpath'

        if not zeek_config:
            zeek_config = find_program('bro-config')
            path_option = '--bropath'

        zeekpath = os.environ.get('ZEEKPATH')

        if not zeekpath:
            zeekpath = os.environ.get('BROPATH')

        pluginpath = os.environ.get('ZEEK_PLUGIN_PATH')

        if not pluginpath:
            pluginpath = os.environ.get('BRO_PLUGIN_PATH')

        if zeek_config:
            cmd = subprocess.Popen([zeek_config, path_option, '--plugin_dir'],
                                   stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT,
                                   bufsize=1, universal_newlines=True)
            line1 = read_zeek_config_line(cmd.stdout)
            line2 = read_zeek_config_line(cmd.stdout)

            if not zeekpath:
                zeekpath = line1

            if not pluginpath:
                pluginpath = line2
        else:
            LOG.warning('zeek-config not found when running tests for %s',
                        package.name)
            return ('no "zeek-config" or "bro-config" found in PATH', False, test_dir)

        zeekpath = os.path.dirname(stage_script_dir) + ':' + zeekpath
        pluginpath = os.path.dirname(stage_plugin_dir) + ':' + pluginpath

        env = os.environ.copy()
        env['ZEEKPATH'] = zeekpath
        env['ZEEK_PLUGIN_PATH'] = pluginpath
        env['BROPATH'] = zeekpath
        env['BRO_PLUGIN_PATH'] = pluginpath
        cwd = os.path.join(clone_dir, package.name)
        outfile = os.path.join(cwd, 'zkg.test_command.stdout')
        errfile = os.path.join(cwd, 'zkg.test_command.stderr')

        LOG.debug('running test_command for %s with cwd="%s"'
                  ' and ZEEKPATH/BROPATH="%s": %s',
                  package.name, cwd, zeekpath, test_command)

        with open(outfile, 'w') as test_stdout, open(errfile, 'w') as test_stderr:
            cmd = subprocess.Popen(test_command, shell=True, cwd=cwd, env=env,
                    stdout=test_stdout, stderr=test_stderr)
        return ('', cmd.wait() == 0, test_dir)

    def _stage(self, package, version, clone,
               stage_script_dir, stage_plugin_dir):
        metadata_file = _pick_metadata_file(clone.working_dir)

        # First use raw parser so no value interpolation takes place.
        raw_metadata_parser = configparser.RawConfigParser()
        invalid_reason = _parse_package_metadata(
            raw_metadata_parser, metadata_file)

        if invalid_reason:
            return invalid_reason

        raw_metadata = _get_package_metadata(raw_metadata_parser)

        requested_user_vars = user_vars(raw_metadata)

        if requested_user_vars is None:
            return "package has malformed 'user_vars' metadata field"

        substitutions = {
            'bro_dist': self.zeek_dist,
            'zeek_dist': self.zeek_dist,
        }
        substitutions.update(self.user_vars)

        for k, v, _ in requested_user_vars:
            val_from_env = os.environ.get(k)

            if val_from_env:
                substitutions[k] = val_from_env

            if k not in substitutions:
                substitutions[k] = v

        metadata_parser = GoodConfigParser(defaults=substitutions)
        invalid_reason = _parse_package_metadata(
            metadata_parser, metadata_file)

        if invalid_reason:
            return invalid_reason

        metadata = _get_package_metadata(metadata_parser)
        LOG.debug('building "%s": version %s', package, version)
        build_command = metadata.get('build_command', '')

        if build_command:
            LOG.debug('building "%s": running build_command: %s',
                      package, build_command)
            bufsize = 4096
            build = subprocess.Popen(build_command,
                                     shell=True, cwd=clone.working_dir,
                                     bufsize=bufsize,
                                     stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE)

            try:
                buildlog = self.package_build_log(clone.working_dir)

                with open(buildlog, 'wb') as f:
                    LOG.info('installing "%s": writing build log: %s',
                             package, buildlog)

                    f.write(u'=== STDERR ===\n'.encode(std_encoding(sys.stderr)))

                    while True:
                        data = build.stderr.read(bufsize)

                        if data:
                            f.write(data)
                        else:
                            break

                    f.write(u'=== STDOUT ===\n'.encode(std_encoding(sys.stdout)))

                    while True:
                        data = build.stdout.read(bufsize)

                        if data:
                            f.write(data)
                        else:
                            break

            except EnvironmentError as error:
                LOG.warning(
                    'installing "%s": failed to write build log %s %s: %s',
                    package, buildlog, error.errno, error.strerror)

            returncode = build.wait()

            if returncode != 0:
                return 'package build_command failed, see log in {}'.format(
                    buildlog)

        pkg_script_dir = metadata.get('script_dir', '')
        script_dir_src = os.path.join(clone.working_dir, pkg_script_dir)
        script_dir_dst = os.path.join(stage_script_dir, package.name)

        if not os.path.exists(script_dir_src):
            return str.format("package's 'script_dir' does not exist: {}",
                              pkg_script_dir)

        pkgload = os.path.join(script_dir_src, '__load__.')

        # Check if __load__.bro exists for compatibility with older packages
        if os.path.isfile(pkgload + 'zeek') or os.path.isfile(pkgload + 'bro'):
            try:
                symlink_path = os.path.join(os.path.dirname(stage_script_dir),
                                            package.name)
                make_symlink(os.path.join('packages', package.name),
                             symlink_path)

                for alias in aliases(metadata):
                    symlink_path = os.path.join(
                            os.path.dirname(stage_script_dir), alias)
                    make_symlink(os.path.join('packages', package.name),
                                 symlink_path)

            except OSError as exception:
                error = 'could not create symlink at {}'.format(symlink_path)
                error += ': {}: {}'.format(type(exception).__name__, exception)
                return error

            error = _copy_package_dir(package, 'script_dir',
                                      script_dir_src, script_dir_dst,
                                      self.scratch_dir)

            if error:
                return error
        else:
            if 'script_dir' in metadata:
                return str.format("no __load__.zeek file found"
                                  " in package's 'script_dir' : {}",
                                  pkg_script_dir)
            else:
                LOG.warning('installing "%s": no __load__.zeek in implicit'
                            ' script_dir, skipped installing scripts', package)

        pkg_plugin_dir = metadata.get('plugin_dir', 'build')
        plugin_dir_src = os.path.join(clone.working_dir, pkg_plugin_dir)
        plugin_dir_dst = os.path.join(stage_plugin_dir, package.name)

        if not os.path.exists(plugin_dir_src):
            LOG.info('installing "%s": package "plugin_dir" does not exist: %s',
                     package, pkg_plugin_dir)

            if pkg_plugin_dir != 'build':
                # It's common for a package to not have build directory for
                # for plugins, so don't error out in that case, just log it.
                return str.format("package's 'plugin_dir' does not exist: {}",
                                  pkg_plugin_dir)

        error = _copy_package_dir(package, 'plugin_dir',
                                  plugin_dir_src, plugin_dir_dst,
                                  self.scratch_dir)

        if error:
            return error

    def install(self, pkg_path, version=''):
        """Install a package.

        Args:
            pkg_path (str): the full git URL of a package or the shortened
                path/name that refers to it within a package source.  E.g. for
                a package source called "zeek" with package named "foo" in
                :file:`alice/zkg.index`, the following inputs may refer
                to the package: "foo", "alice/foo", or "zeek/alice/foo".

            version (str): if not given, then the latest git version tag is
                installed (or if no version tags exist, the "master" branch is
                installed).  If given, it may be either a git version tag, a
                git branch name, or a git commit hash.

        Returns:
            str: empty string if package installation succeeded else an error
            string explaining why it failed.

        Raises:
            IOError: if the manifest can't be written
        """
        pkg_path = canonical_url(pkg_path)
        LOG.debug('installing "%s"', pkg_path)
        ipkg = self.find_installed_package(pkg_path)

        if ipkg:
            conflict = ipkg.package

            if conflict.qualified_name().endswith(pkg_path):
                LOG.debug('installing "%s": re-install: %s',
                          pkg_path, conflict)
                clonepath = os.path.join(self.package_clonedir, conflict.name)
                _clone_package(conflict, clonepath, version)
                return self._install(conflict, version)
            else:
                LOG.info(
                    'installing "%s": matched already installed package: %s',
                    pkg_path, conflict)
                return str.format(
                    'package with name "{}" ({}) is already installed',
                    conflict.name, conflict)

        matches = self.match_source_packages(pkg_path)

        if not matches:
            try:
                package = Package(git_url=pkg_path)
                return self._install(package, version)
            except git.exc.GitCommandError as error:
                LOG.info('installing "%s": invalid git repo path: %s', pkg_path,
                         error)

            LOG.info('installing "%s": matched no source package', pkg_path)
            return 'package not found in sources and also not a valid git URL'

        if len(matches) > 1:
            matches_string = [match.qualified_name() for match in matches]
            LOG.info('installing "%s": matched multiple packages: %s',
                     pkg_path, matches_string)
            return str.format('"{}" matches multiple packages, try a more'
                              ' specific name from: {}',
                              pkg_path, matches_string)

        try:
            return self._install(matches[0], version)
        except git.exc.GitCommandError as error:
            LOG.warning('installing "%s": source package git repo is invalid',
                        pkg_path)
            return 'failed to clone package "{}": {}'.format(pkg_path, error)

        return ''

    def _install(self, package, version, use_existing_clone=False):
        """Install a :class:`.package.Package`.

        Returns:
            str: empty string if package installation succeeded else an error
            string explaining why it failed.

        Raises:
            git.exc.GitCommandError: if the git repo is invalid
            IOError: if the package manifest file can't be written
        """
        clonepath = os.path.join(self.package_clonedir, package.name)
        ipkg = self.find_installed_package(package.name)

        if use_existing_clone or ipkg:
            clone = git.Repo(clonepath)
        else:
            clone = _clone_package(package, clonepath, version)

        status = PackageStatus()
        status.is_loaded = ipkg.status.is_loaded if ipkg else False
        status.is_pinned = ipkg.status.is_pinned if ipkg else False

        version_tags = _get_version_tags(clone)

        if version:
            if _is_commit_hash(clone, version):
                status.tracking_method = TRACKING_METHOD_COMMIT
            elif version in version_tags:
                status.tracking_method = TRACKING_METHOD_VERSION
            else:
                branches = _get_branch_names(clone)

                if version in branches:
                    status.tracking_method = TRACKING_METHOD_BRANCH
                else:
                    LOG.info(
                        'branch "%s" not in available branches: %s', version,
                        branches)
                    return 'no such branch or version tag: "{}"'.format(version)

        else:
            if len(version_tags):
                version = version_tags[-1]
                status.tracking_method = TRACKING_METHOD_VERSION
            else:
                if 'master' not in _get_branch_names(clone):
                    return 'git repo has no "master" branch or version tags'

                version = 'master'
                status.tracking_method = TRACKING_METHOD_BRANCH

        status.current_version = version
        git_checkout(clone, version)
        status.current_hash = clone.head.object.hexsha
        status.is_outdated = _is_clone_outdated(
            clone, version, status.tracking_method)

        metadata_file = _pick_metadata_file(clone.working_dir)
        # Use raw parser so no value interpolation takes place.
        raw_metadata_parser = configparser.RawConfigParser()
        invalid_reason = _parse_package_metadata(
            raw_metadata_parser, metadata_file)

        if invalid_reason:
            return invalid_reason

        raw_metadata = _get_package_metadata(raw_metadata_parser)

        fail_msg = self._stage(package, version, clone, self.script_dir,
                               self.plugin_dir)

        if fail_msg:
            return fail_msg

        if not package.source:
            # If installing directly from git URL, see if it actually is found
            # in a package source and fill in those details.
            for pkg in self.source_packages():
                if pkg.git_url == package.git_url:
                    package.source = pkg.source
                    package.directory = pkg.directory
                    package.metadata = pkg.metadata
                    break

        package.metadata = raw_metadata
        self.installed_pkgs[package.name] = InstalledPackage(package, status)
        self._write_manifest()
        LOG.debug('installed "%s"', package)
        return ''

def _normalize_version_tag(tag):
    # Change vX.Y.Z into X.Y.Z
    if len(tag) > 1 and tag[0] == 'v' and tag[1].isdigit():
        return tag[1:]

    return tag

def _get_version_tags(clone):
    tags = []

    for tagref in clone.tags:
        tag = str(tagref.name)
        normal_tag = _normalize_version_tag(tag)

        try:
            sv = semver.Version.coerce(normal_tag)
        except ValueError:
            # Skip tags that aren't compatible semantic versions.
            continue
        else:
            tags.append((normal_tag, tag, sv))

    return [t[1] for t in sorted(tags, key=lambda e: e[2])]


def _get_branch_names(clone):
    rval = []

    for ref in clone.references:
        branch_name = str(ref.name)

        if not branch_name.startswith('origin/'):
            continue

        rval.append(branch_name.split('origin/')[1])

    return rval


def _is_version_outdated(clone, version):
    version_tags = _get_version_tags(clone)
    latest = _normalize_version_tag(version_tags[-1])
    return _normalize_version_tag(version) != latest


def _is_branch_outdated(clone, branch):
    it = clone.iter_commits('{0}..origin/{0}'.format(branch))
    num_commits_behind = sum(1 for c in it)
    return num_commits_behind > 0


def _is_clone_outdated(clone, ref_name, tracking_method):
    if tracking_method == TRACKING_METHOD_VERSION:
        return _is_version_outdated(clone, ref_name)
    elif tracking_method == TRACKING_METHOD_BRANCH:
        return _is_branch_outdated(clone, ref_name)
    elif tracking_method == TRACKING_METHOD_COMMIT:
        return False
    else:
        raise NotImplementedError


def _is_commit_hash(clone, text):
    try:
        commit = clone.commit(text)
        return commit.hexsha.startswith(text)
    except Exception:
        return False


def _copy_package_dir(package, dirname, src, dst, scratch_dir):
    """Copy a directory from a package to its installation location.

    Returns:
        str: empty string if package dir copy succeeded else an error string
        explaining why it failed.
    """
    if not os.path.exists(src):
        return ''

    if os.path.isfile(src) and tarfile.is_tarfile(src):
        tmp_dir = os.path.join(scratch_dir, 'untar')
        delete_path(tmp_dir)
        make_dir(tmp_dir)

        try:
            with tarfile.open(src) as tf:
                tf.extractall(tmp_dir)
        except Exception as error:
            return str(error)

        ld = os.listdir(tmp_dir)

        if len(ld) != 1:
            return 'failed to copy package {}: invalid tarfile'.format(dirname)

        src = os.path.join(tmp_dir, ld[0])

    if not os.path.isdir(src):
        return 'failed to copy package {}: not a dir or tarfile'.format(dirname)

    def ignore(_, files):
        rval = []

        for f in files:
            if f in {'.git', 'bro-pkg.meta', 'zkg.meta'}:
                rval.append(f)

        return rval

    try:
        copy_over_path(src, dst, ignore=ignore)
    except shutil.Error as error:
        errors = error.args[0]
        reasons = ""

        for err in errors:
            src, dst, msg = err
            reason = 'failed to copy {}: {} -> {}: {}'.format(
                dirname, src, dst, msg)
            reasons += '\n' + reason
            LOG.warning('installing "%s": %s', package, reason)

        return 'failed to copy package {}: {}'.format(dirname, reasons)

    return ''


def _create_readme(file_path):
    if os.path.exists(file_path):
        return

    with open(file_path, 'w') as f:
        f.write('WARNING: This directory is managed by zkg.\n')
        f.write("Don't make direct modifications to anything within it.\n")


def _clone_package(package, clonepath, version):
    """Clone a :class:`.package.Package` git repo.

    Returns:
        git.Repo: the cloned package

    Raises:
        git.exc.GitCommandError: if the git repo is invalid
    """
    delete_path(clonepath)
    shallow = not is_sha1(version)
    return git_clone(package.git_url, clonepath, shallow=shallow)


def _get_package_metadata(parser):
    metadata = {item[0]: item[1] for item in parser.items('package')}
    return metadata


def _pick_metadata_file(directory):
    rval = os.path.join(directory, METADATA_FILENAME)

    if os.path.exists(rval):
        return rval

    return os.path.join(directory, LEGACY_METADATA_FILENAME)


def _parse_package_metadata(parser, metadata_file):
    """Return string explaining why metadata is invalid, or '' if valid. """
    if not parser.read(metadata_file):
        LOG.warning('%s: missing metadata file', metadata_file)
        return 'missing {} (or {}) metadata file'.format(
                METADATA_FILENAME, LEGACY_METADATA_FILENAME)

    if not parser.has_section('package'):
        LOG.warning('%s: metadata missing [package]', metadata_file)
        return '{} is missing [package] section'.format(
                os.path.basename(metadata_file))

    return ''


def _info_from_clone(clone, package, status, version):
    """Retrieves information about a package.

    Returns:
        A :class:`.package.PackageInfo` object.
    """
    versions = _get_version_tags(clone)

    if _is_commit_hash(clone, version):
        version_type = TRACKING_METHOD_COMMIT
    elif version in versions:
        version_type = TRACKING_METHOD_VERSION
    else:
        version_type = TRACKING_METHOD_BRANCH

    metadata_file = _pick_metadata_file(clone.working_dir)
    # Use raw parser so no value interpolation takes place.
    metadata_parser = configparser.RawConfigParser()
    invalid_reason = _parse_package_metadata(
        metadata_parser, metadata_file)

    if invalid_reason:
        return PackageInfo(package=package, invalid_reason=invalid_reason,
                           status=status, versions=versions,
                           metadata_version=version, version_type=version_type,
                           metadata_file=metadata_file)

    metadata = _get_package_metadata(metadata_parser)

    return PackageInfo(package=package, invalid_reason=invalid_reason,
                       status=status, metadata=metadata, versions=versions,
                       metadata_version=version, version_type=version_type,
                       metadata_file=metadata_file)
