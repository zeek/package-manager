"""
A module defining the main Bro Package Manager interface which supplies methods
to interact with and operate on Bro packages.
"""

import sys
import os
import json
import shutil

if sys.version_info[0] < 3:
    import ConfigParser as configparser
else:
    import configparser

import git
import semantic_version as semver

from ._util import (
    make_dir,
    remove_trailing_slashes,
    delete_path,
    make_symlink,
    copy_over_path,
)
from .source import Source
from .package import Package, PackageInfo, PackageStatus, InstalledPackage
from . import (
    __version__,
    LOG,
)


class Manager(object):
    """A package manager object performs various operations on packages.

    It uses a state directory and a manifest file within it to keep
    track of package sources, installed packages and their statuses.

    Attributes:
        sources (dict of str -> str): dictionary package sources whose
            keys and values are the names and git URL, respectively, given to
            :meth:`add_source()`

        installed_pkgs (dict of str -> :class:`.package.InstalledPackage`):
            a dictionary of installed packaged keyed on package names (the last
            component of the package's git URL)

        bro_dist (str): path to the Bro source code distribution.  This
            is needed for packages that contain Bro plugins that need to be
            built from source code.

        statedir (str): the directory where the package manager will
            a maintain manifest file, package/source git clones, and other
            persistent state the manager needs in order to operate

        scratchdir (str): a directory where the package manager performs
            miscellaneous/temporary file operations

        scriptdir (str): the directory where the package manager will
            copy each installed package's `scriptpath` (as given by its
            :file:`pkg.meta` file).  Each package gets a subdirectory within
            `scriptdir` associated with its name.

        plugindir (str): the directory where the package manager will
            copy each installed package's `pluginpath` (as given by its
            :file:`pkg.meta` file).  Each package gets a subdirectory within
            `plugindir` associated with its name.

        source_clone_dir (str): the directory where the package manager
            will clone package sources.  Each source gets a subdirectory
            associated with its name.

        package_clone_dir (str): the directory where the package manager
            will clone installed packages.  Each package gets a subdirectory
            associated with its name.

        manifest (str): the path to the package manager's manifest file.
            This file maintains a list of installed packages and their status.

        autoload_script (str): path to a Bro script named :file:`packages.bro`
            that the package manager maintains.  It is a list of ``@load`` for
            each installed package that is marked as loaded (see
            :meth:`load()`).

        autoload_package (str): path to a Bro :file:`__load__.bro` script
            which is just a symlink to `autoload_script`.  It's always located
            in a directory named :file:`packages`, so as long as
            :envvar:`BROPATH` is configured correctly, ``@load packages`` will
            load all installed packages that have been marked as loaded.

        pkg_metadata_filename (str): the expected file name that packages
            are supposed to use to store their metadata
    """

    def __init__(self, statedir, scriptdir, plugindir, bro_dist=''):
        """Creates a package manager instance.

        Args:
            statedir (str): value to set the `statedir` attribute to

            scriptdir (str): value to set the `scriptdir` attribute to

            plugindir (str): value to set the `plugindir` attribute to

            bro_dist (str): value to set the `bro_dist` attribute to

        Raises:
            OSError: when a package manager state directory can't be created
            IOError: when a package manager state file can't be created
        """
        LOG.debug('init Manager version %s', __version__)
        self.sources = {}
        self.installed_pkgs = {}
        self.bro_dist = bro_dist
        self.statedir = statedir
        self.scratchdir = os.path.join(self.statedir, 'scratch')
        self.scriptdir = os.path.join(scriptdir, 'packages')
        self.plugindir = os.path.join(plugindir, 'packages')
        self.source_clonedir = os.path.join(self.statedir, 'clones', 'source')
        self.package_clonedir = os.path.join(
            self.statedir, 'clones', 'package')
        self.manifest = os.path.join(self.statedir, 'manifest.json')
        self.autoload_script = os.path.join(self.scriptdir, 'packages.bro')
        self.autoload_package = os.path.join(self.scriptdir, '__load__.bro')
        self.pkg_metadata_filename = 'pkg.meta'
        make_dir(self.statedir)
        make_dir(self.scratchdir)
        make_dir(self.source_clonedir)
        make_dir(self.package_clonedir)
        make_dir(self.scriptdir)
        make_dir(self.plugindir)

        if not os.path.exists(self.manifest):
            self._write_manifest()

        prev_scriptdir, prev_plugindir = self._read_manifest()

        if prev_scriptdir != self.scriptdir:
            LOG.info('moved previous scriptdir %s -> %s', prev_scriptdir,
                     self.scriptdir)

            if os.path.exists(prev_scriptdir):
                delete_path(self.scriptdir)
                shutil.move(prev_scriptdir, self.scriptdir)
                prev_bropath = os.path.dirname(prev_scriptdir)

                for pkg_name in self.installed_pkgs:
                    shutil.move(os.path.join(prev_bropath, pkg_name),
                                os.path.join(self.bropath(), pkg_name))

            self._write_manifest()

        if prev_plugindir != self.plugindir:
            LOG.info('moved previous plugindir %s -> %s', prev_plugindir,
                     self.plugindir)

            if os.path.exists(prev_plugindir):
                delete_path(self.plugindir)
                shutil.move(prev_plugindir, self.plugindir)

            self._write_manifest()

        self._write_autoloader()
        make_symlink('packages.bro', self.autoload_package)

    def _write_autoloader(self):
        """Write the :file:`__load__.bro` loader script.

        Raises:
            IOError: if :file:`__load__.bro` loader script cannot be written
        """
        with open(self.autoload_script, 'w') as f:
            content = ""

            for ipkg in self.loaded_packages():
                content += '@load ./{}\n'.format(ipkg.package.name)

            f.write(content)

    def _read_manifest(self):
        """Read the manifest file containing the list of installed packages.

        Returns:
            tuple: (previous scriptdir, previous plugindir)

        Raises:
            IOError: when the manifest file can't be read
        """
        with open(self.manifest, 'r') as f:
            data = json.load(f)
            pkg_list = data['installed_packages']
            self.installed_pkgs = {}

            for dicts in pkg_list:
                pkg_dict = dicts['package_dict']
                status_dict = dicts['status_dict']

                pkg_name = pkg_dict['name']
                del pkg_dict['name']

                pkg = Package(**pkg_dict)
                status = PackageStatus(**status_dict)
                self.installed_pkgs[pkg_name] = InstalledPackage(pkg, status)

            return data['scriptdir'], data['plugindir']

    def _write_manifest(self):
        """Writes the manifest file containing the list of installed packages.

        Raises:
            IOError: when the manifest file can't be written
        """
        pkg_list = []

        for _, installed_pkg in self.installed_pkgs.items():
            pkg_list.append({'package_dict': installed_pkg.package.__dict__,
                             'status_dict': installed_pkg.status.__dict__})

        data = {'manifest_version': 0, 'scriptdir': self.scriptdir,
                'plugindir': self.plugindir, 'installed_packages': pkg_list}

        with open(self.manifest, 'w') as f:
            json.dump(data, f)

    def bropath(self):
        """Return the path where installed package scripts are located.

        This path can be added to :envvar:`BROPATH` for interoperability with
        Bro.
        """
        return os.path.dirname(self.scriptdir)

    def bro_plugin_path(self):
        """Return the path where installed package plugins are located.

        This path can be added to :envvar:`BRO_PLUGIN_PATH` for
        interoperability with Bro.
        """
        return os.path.dirname(self.plugindir)

    def add_source(self, name, git_url):
        """Add a git repository that acts as a source of packages.

        Args:
            name (str): a short name that will be used to reference the package
                source.

            git_url (str): the git URL of the package source

        Returns:
            bool: True if the source is successfully added.  It may fail to be
            added if the git URL is invalid or if a source with a different
            git URL already exists with the same name.
        """
        if name in self.sources:
            existing_source = self.sources[name]

            if existing_source.git_url == git_url:
                LOG.debug('duplicate source "%s"', name)
                return True

            LOG.warning('conflicting source URLs with name "%s": %s and %s',
                        name, git_url, existing_source.git_url)
            return False

        clone_path = os.path.join(self.source_clonedir, name)

        try:
            source = Source(name=name, clone_path=clone_path, git_url=git_url)
        except git.exc.GitCommandError as error:
            LOG.warning('failed to clone source "%s", git url %s: %s', name,
                        git_url, error)
            return False
        else:
            self.sources[name] = source

        return True

    def source_packages(self):
        """Return a list of :class:`.package.Package` within all sources."""
        rval = []

        for _, source in self.sources.items():
            rval += source.packages()

        return rval

    def installed_packages(self):
        """Return list of :class:`.package.InstalledPackage`."""
        return [ipkg for _, ipkg in self.installed_pkgs.items()]

    def loaded_packages(self):
        """Return list of loaded :class:`.package.InstalledPackage`."""
        rval = []

        for _, ipkg in self.installed_pkgs.items():
            if ipkg.status.is_loaded:
                rval.append(ipkg)

        return rval

    def package_build_log(self, pkg_path):
        """Return the path to the package manager's build log for a package.

        Args:
            pkg_path (str): the full git URL of a package or the shortened
                path/name that refers to it within a package source.  E.g. for
                a package in a source named "bro" at submodule path "alice/foo",
                the following inputs may refer to the package: "foo",
                "alice/foo", or "bro/alice/foo".
        """
        name = Package.name_from_path(pkg_path)
        return os.path.join(self.package_clonedir, '.build-{}.log'.format(name))

    def match_source_packages(self, pkg_path):
        """Return a list of :class:`.package.Package` that match a given path.

        Args:
            pkg_path (str): the full git URL of a package or the shortened
                path/name that refers to it within a package source.  E.g. for
                a package in a source named "bro" at submodule path "alice/foo",
                the following inputs may refer to the package: "foo",
                "alice/foo", or "bro/alice/foo".
        """
        rval = []

        for pkg in self.source_packages():
            if pkg.matches_path(pkg_path):
                rval.append(pkg)

        return rval

    def find_installed_package(self, pkg_path):
        """Return an :class:`.package.InstalledPackage` if one matches the name.

        Args:
            pkg_path (str): the full git URL of a package or the shortened
                path/name that refers to it within a package source.  E.g. for
                a package in a source named "bro" at submodule path "alice/foo",
                the following inputs may refer to the package: "foo",
                "alice/foo", or "bro/alice/foo".

        A package's name is the last component of it's git URL.
        """
        pkg_name = Package.name_from_path(pkg_path)
        return self.installed_pkgs.get(pkg_name)

    def refresh(self):
        """Fetch latest git versions for sources and installed packages.

        This retrieves information about new packages and new versions of
        existing packages, but does not yet upgrade installed packaged.

        Raises:
            IOError: if the package manifest file can't be written
        """
        for name, source in self.sources.items():
            LOG.debug('refresh "%s": pulling %s', name, source.git_url)
            source.clone.remote().pull()

        for ipkg in self.installed_packages():
            clonepath = os.path.join(self.package_clonedir, ipkg.package.name)
            clone = git.Repo(clonepath)
            clone.remote().fetch()
            ipkg.status.is_outdated = _is_clone_outdated(
                clone, ipkg.status.current_version, ipkg.status.tracking_method)

        self._write_manifest()

    def upgrade(self, pkg_path):
        """Upgrade a package to the latest available version.

        Args:
            pkg_path (str): the full git URL of a package or the shortened
                path/name that refers to it within a package source.  E.g. for
                a package in a source named "bro" at submodule path "alice/foo",
                the following inputs may refer to the package: "foo",
                "alice/foo", or "bro/alice/foo".

        Returns:
            str: an empty string if package upgrade succeeded else an error
            string explaining why it failed.

        Raises:
            IOError: if the manifest can't be written
        """
        LOG.debug('upgrading "%s"', pkg_path)
        pkg_path = remove_trailing_slashes(pkg_path)
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

        if ipkg.status.tracking_method == 'version':
            version_tags = _get_version_tags(clone)
            return self._install(ipkg.package, version_tags[-1])
        elif ipkg.status.tracking_method == 'branch':
            clone.remote().pull()
            return self._install(ipkg.package, ipkg.status.current_version)
        else:
            raise NotImplementedError

    def remove(self, pkg_path):
        """Remove an installed package.

        Args:
            pkg_path (str): the full git URL of a package or the shortened
                path/name that refers to it within a package source.  E.g. for
                a package in a source named "bro" at submodule path "alice/foo",
                the following inputs may refer to the package: "foo",
                "alice/foo", or "bro/alice/foo".

        Returns:
            bool: True if an installed package was removed, else False.

        Raises:
            IOError: if the package manifest file can't be written
            OSError: if the installed package's directory can't be deleted
        """
        LOG.debug('removing "%s"', pkg_path)
        pkg_path = remove_trailing_slashes(pkg_path)
        ipkg = self.find_installed_package(pkg_path)

        if not ipkg:
            LOG.info('removing "%s": could not find matching package', pkg_path)
            return False

        self.unload(pkg_path)

        pkg_to_remove = ipkg.package
        delete_path(os.path.join(self.package_clonedir, pkg_to_remove.name))
        delete_path(os.path.join(self.scriptdir, pkg_to_remove.name))
        delete_path(os.path.join(self.plugindir, pkg_to_remove.name))
        delete_path(os.path.join(self.bropath(), pkg_to_remove.name))

        del self.installed_pkgs[pkg_to_remove.name]
        self._write_manifest()

        # @todo: check dependencies
        LOG.debug('removed "%s"', pkg_path)
        return True

    def pin(self, pkg_path):
        """Pin a currently installed package to the currently installed version.

        Pinned packages are never upgraded when calling :meth:`upgrade()`.

        Args:
            pkg_path (str): the full git URL of a package or the shortened
                path/name that refers to it within a package source.  E.g. for
                a package in a source named "bro" at submodule path "alice/foo",
                the following inputs may refer to the package: "foo",
                "alice/foo", or "bro/alice/foo".

        Returns:
            :class:`.package.InstalledPackage`: if successfully pinned or
            None if no matching installed package could be found.

        Raises:
            IOError: when the manifest file can't be written
        """
        LOG.debug('pinning "%s"', pkg_path)
        pkg_path = remove_trailing_slashes(pkg_path)
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
                a package in a source named "bro" at submodule path "alice/foo",
                the following inputs may refer to the package: "foo",
                "alice/foo", or "bro/alice/foo".

        Returns:
            :class:`.package.InstalledPackage`: if successfully unpinned or
            None if no matching installed package could be found.

        Raises:
            IOError: when the manifest file can't be written
        """
        LOG.debug('unpinning "%s"', pkg_path)
        pkg_path = remove_trailing_slashes(pkg_path)
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

        The collection of "loaded" packages is a convenient way for Bro to more
        simply load a whole group of packages installed via the package manager.

        Args:
            pkg_path (str): the full git URL of a package or the shortened
                path/name that refers to it within a package source.  E.g. for
                a package in a source named "bro" at submodule path "alice/foo",
                the following inputs may refer to the package: "foo",
                "alice/foo", or "bro/alice/foo".

        Returns:
            bool: True if a package is successfully marked as loaded.

        Raises:
            IOError: if the loader script or manifest can't be written
        """
        LOG.debug('loading "%s"', pkg_path)
        pkg_path = remove_trailing_slashes(pkg_path)
        ipkg = self.find_installed_package(pkg_path)

        if not ipkg:
            LOG.info('loading "%s": no matching package', pkg_path)
            return False

        if ipkg.status.is_loaded:
            LOG.debug('loading "%s": already loaded', pkg_path)
            return True

        ipkg.status.is_loaded = True
        self._write_autoloader()
        self._write_manifest()
        LOG.debug('loaded "%s"', pkg_path)
        return True

    def unload(self, pkg_path):
        """Unmark an installed package as being "loaded".

        The collection of "loaded" packages is a convenient way for Bro to more
        simply load a whole group of packages installed via the package manager.

        Args:
            pkg_path (str): the full git URL of a package or the shortened
                path/name that refers to it within a package source.  E.g. for
                a package in a source named "bro" at submodule path "alice/foo",
                the following inputs may refer to the package: "foo",
                "alice/foo", or "bro/alice/foo".

        Returns:
            bool: True if a package is successfully unmarked as loaded.

        Raises:
            IOError: if the loader script or manifest can't be written
        """
        LOG.debug('unloading "%s"', pkg_path)
        pkg_path = remove_trailing_slashes(pkg_path)
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
        LOG.debug('unloaded "%s"', pkg_path)
        return True

    def info(self, pkg_path):
        """Retrieves information about a package.

        Args:
            pkg_path (str): the full git URL of a package or the shortened
                path/name that refers to it within a package source.  E.g. for
                a package in a source named "bro" at submodule path "alice/foo",
                the following inputs may refer to the package: "foo",
                "alice/foo", or "bro/alice/foo".

        Returns:
            A :class:`.package.PackageInfo` object.
        """
        LOG.debug('getting info on "%s", pkg_path')
        pkg_path = remove_trailing_slashes(pkg_path)
        ipkg = self.find_installed_package(pkg_path)

        if ipkg:
            status = ipkg.status
            matches = [ipkg.package]
        else:
            status = None
            matches = self.match_source_packages(pkg_path)

        if not matches:
            package = Package(git_url=pkg_path)

            try:
                return self._info(package, status)
            except git.exc.GitCommandError as error:
                LOG.info('getting info on "%s": invalid git repo path: %s',
                         pkg_path, error)

            LOG.info('getting info on "%s": matched no source package',
                     pkg_path)
            reason = 'package not found in sources and not a valid git URL'
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
        return self._info(package, status)

    def _info(self, package, status):
        """Retrieves information about a package.

        Returns:
            A :class:`.package.PackageInfo` object.

        Raises:
            git.exc.GitCommandError: when failing to clone the package repo
        """
        clonepath = os.path.join(self.scratchdir, package.name)
        invalid_reason = self._clone_package(package, clonepath)
        return PackageInfo(package=package, invalid_reason=invalid_reason,
                           status=status)

    def _clone_package(self, package, clonepath):
        """Clone :class:`.package.Package` git repo and retrieve metadata/info.

        Returns:
            str: empty string if package cloning and metadata/info retrieval
            succeeded else an error string explaining why it failed.

        Raises:
            git.exc.GitCommandError: if the git repo is invalid
        """
        delete_path(clonepath)
        clone = git.Repo.clone_from(package.git_url, clonepath)

        default_metadata = {'bro_dist': self.bro_dist, 'scriptpath': '',
                            'pluginpath': 'build', 'buildcmd': ''}
        parser = configparser.SafeConfigParser(defaults=default_metadata)
        metadata_file = os.path.join(clonepath, self.pkg_metadata_filename)

        if not parser.read(metadata_file):
            LOG.warning('cloning "%s": no metadata file', package)
            return 'missing pkg.meta metadata file'

        if not parser.has_section('package'):
            LOG.warning('cloning "%s": metadata missing [package]', package)
            return 'pkg.meta metadata file is missing [package] section'

        metadata = {item[0]: item[1] for item in parser.items('package')}
        package.metadata = metadata
        package.versions = _get_version_tags(clone)
        return ''

    def install(self, pkg_path, version=None):
        """Install a package.

        Args:
            pkg_path (str): the full git URL of a package or the shortened
                path/name that refers to it within a package source.  E.g. for
                a package in a source named "bro" at submodule path "alice/foo",
                the following inputs may refer to the package: "foo",
                "alice/foo", or "bro/alice/foo".

            version (str): if not given, then the latest git version tag is
                installed (or if no version tags exist, the "master" branch is
                installed).  If given, it may be either a git version tag or a
                git branch name.

        Returns:
            str: empty string if package installation succeeded else an error
            string explaining why it failed.

        Raises:
            IOError: if the manifest can't be written
        """
        LOG.debug('installing "%s"', pkg_path)
        pkg_path = remove_trailing_slashes(pkg_path)
        ipkg = self.find_installed_package(pkg_path)

        if ipkg:
            conflict = ipkg.package

            if conflict.qualified_name().endswith(pkg_path):
                LOG.debug('installing "%s": re-install: %s',
                          pkg_path, conflict)
                return self._install(ipkg.package, version)
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

        # @todo: install dependencies
        return ''

    def _install(self, package, version=None):
        """Install a :class:`.package.Package`.

        Returns:
            str: empty string if package installation succeeded else an error
            string explaining why it failed.

        Raises:
            git.exc.GitCommandError: if the git repo is invalid
            IOError: if the package manifest file can't be written
        """
        # @todo: check if dependencies would be broken by overwriting a
        # previous installed package w/ a new version
        clonepath = os.path.join(self.package_clonedir, package.name)
        ipkg = self.find_installed_package(package.name)

        if not ipkg:
            res = self._clone_package(package, clonepath)

            if res:
                return res

        clone = git.Repo(clonepath)
        version_tags = _get_version_tags(clone)
        status = PackageStatus()
        status.is_loaded = ipkg.status.is_loaded if ipkg else False
        status.is_pinned = ipkg.status.is_pinned if ipkg else False

        if version:
            if version in version_tags:
                status.tracking_method = 'version'
            else:
                branches = _get_branch_names(clone)

                if version in branches:
                    status.tracking_method = 'branch'
                else:
                    return 'no such branch or version tag: "{}"'.format(version)

        else:
            if len(version_tags):
                version = version_tags[-1]
                status.tracking_method = 'version'
            else:
                if 'master' not in _get_branch_names(clone):
                    return 'git repo has no "master" branch or version tags'

                version = 'master'
                status.tracking_method = 'branch'

        status.current_version = version
        status.current_hash = _get_hash(clone, version)
        clone.git.checkout(version)
        status.is_outdated = _is_clone_outdated(
            clone, version, status.tracking_method)
        buildcmd = package.metadata['buildcmd']

        if buildcmd:
            import subprocess
            LOG.debug('installing "%s": running buildcmd: %s',
                      package, buildcmd)
            build = subprocess.Popen(buildcmd,
                                     shell=True, cwd=clonepath, bufsize=1,
                                     stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE)

            try:
                buildlog = self.package_build_log(clonepath)

                with open(buildlog, 'w') as f:
                    LOG.warning('installing "%s": writing build log: %s',
                                package, buildlog)

                    f.write('=== STDERR ===\n')

                    for line in build.stderr:
                        f.write(line.decode())

                    f.write('=== STDOUT ===\n')

                    for line in build.stdout:
                        f.write(line.decode())

            except EnvironmentError as error:
                LOG.warning(
                    'installing "%s": failed to write build log %s %s: %s',
                    package, buildlog, error.errno, error.strerror)

            returncode = build.wait()

            if returncode != 0:
                return 'package buildcmd failed, see log in {}'.format(buildlog)

        scriptpath_src = os.path.join(
            clonepath, package.metadata['scriptpath'])
        scriptpath_dst = os.path.join(self.scriptdir, package.name)

        if not os.path.exists(scriptpath_src):
            return str.format("package's 'scriptpath' does not exist: {0}",
                              package.metadata['scriptpath'])

        error = _copy_package_dir(package, 'scriptpath',
                                  scriptpath_src, scriptpath_dst)
        make_symlink(os.path.join('packages', package.name),
                     os.path.join(self.bropath(), package.name))

        if error:
            return error

        pluginpath = package.metadata['pluginpath']
        pluginpath_src = os.path.join(clonepath, pluginpath)
        pluginpath_dst = os.path.join(self.plugindir, package.name)

        if not os.path.exists(pluginpath_src):
            LOG.info('installing "%s": package "pluginpath" does not exist: %s',
                     package, pluginpath)

            if pluginpath != 'build':
                # It's common for a package to not have build directory for
                # for plugins, so don't error out in that case, just log it.
                return str.format("package's 'pluginpath' does not exist: {0}",
                                  pluginpath)

        error = _copy_package_dir(package, 'pluginpath',
                                  pluginpath_src, pluginpath_dst)

        if error:
            return error

        self.installed_pkgs[package.name] = InstalledPackage(package, status)
        self._write_manifest()
        LOG.debug('installed "%s"', package)
        return ''


def _get_version_tags(clone):
    tags = []

    for tagref in clone.tags:
        tag = tagref.name

        try:
            semver.Version.coerce(tag)
        except ValueError:
            # Skip tags that aren't compatible semantic versions.
            continue
        else:
            tags.append(tag)

    return sorted(tags)


def _get_branch_names(clone):
    rval = []

    for ref in clone.references:
        branch_name = ref.name

        if not branch_name.startswith('origin/'):
            continue

        rval.append(branch_name.split('/')[-1])

    return rval


def _get_ref(clone, ref_name):
    for ref in clone.refs:
        if ref.name.split('/')[-1] == ref_name:
            return ref


def _is_version_outdated(clone, version):
    version_tags = _get_version_tags(clone)
    return version != version_tags[-1]


def _is_branch_outdated(clone, branch):
    it = clone.iter_commits('{0}..origin/{0}'.format(branch))
    num_commits_behind = sum(1 for c in it)
    return num_commits_behind > 0


def _is_clone_outdated(clone, ref_name, tracking_method):
    if tracking_method == 'version':
        return _is_version_outdated(clone, ref_name)
    elif tracking_method == 'branch':
        return _is_branch_outdated(clone, ref_name)
    else:
        raise NotImplementedError


def _get_hash(clone, ref_name):
    return _get_ref(clone, ref_name).object.hexsha


def _copy_package_dir(package, dirname, src, dst):
    """Copy a directory from a package to its installation location.

    Returns:
        str: empty string if package dir copy succeeded else an error string
        explaining why it failed.
    """
    try:
        if os.path.exists(src):
            copy_over_path(src, dst)
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
