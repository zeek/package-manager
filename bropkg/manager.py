import sys
import os
import json
import shutil
import subprocess

if sys.version_info[0] < 3:
    import ConfigParser as configparser
else:
    import configparser

import git

from .util import (
    make_dir,
    remove_trailing_slashes,
    delete_path,
    make_symlink,
    copy_over_path,
)
from .source import Source
from .package import Package
from . import (
    __version__,
    LOG,
)


class Manager(object):

    def __init__(self, statedir, scriptdir, plugindir, bro_dist=''):
        """Create package manager.

        :raise OSError: when a package manager state directory can't be created
        :raise IOError: when a package manager state file can't be created
        """
        LOG.debug('init Manager version %s', __version__)
        self.sources = {}
        self.installed_pkgs = {}
        self.loaded_pkgs = {}
        self.bro_dist = bro_dist
        self.statedir = statedir
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

        with open(self.autoload_script, 'a+') as f:
            f.seek(0)
            loaded_pkg_names = [line.split()[1][2:] for line in f]
            new_loaded_pkg_names = []

            for pkg_name in loaded_pkg_names:
                pkg = self.installed_pkgs.get(pkg_name)

                if pkg:
                    LOG.debug('found loaded package: %s', pkg_name)
                    self.loaded_pkgs[pkg_name] = pkg
                    new_loaded_pkg_names.append(pkg_name)
                else:
                    LOG.info('removing orphaned loaded package: %s', pkg_name)

            if len(loaded_pkg_names) != len(new_loaded_pkg_names):
                f.truncate(0)
                content = ""

                for pkg_name in new_loaded_pkg_names:
                    content += '@load ./{}\n'.format(pkg_name)

                f.write(content)

        make_symlink('packages.bro', self.autoload_package)

    def _read_manifest(self):
        """Read the manifest file containing the list of installed packages.

        Returns a tuple of (previous scriptdir, previous plugindir)

        :raise IOError: when the manifest file can't be read
        """
        with open(self.manifest, 'r') as f:
            data = json.load(f)
            pkg_list = data['installed_packages']
            self.installed_pkgs = {}

            for pkg_dict in pkg_list:
                pkg_name = pkg_dict['name']
                pkg_git_url = pkg_dict['git_url']
                pkg_source = pkg_dict['source']
                pkg_module_dir = pkg_dict['module_dir']
                pkg_metadata = pkg_dict['metadata']
                pkg = Package(git_url=pkg_git_url, source=pkg_source,
                              module_dir=pkg_module_dir, metadata=pkg_metadata)
                self.installed_pkgs[pkg_name] = pkg

            return data['scriptdir'], data['plugindir']

    def _write_manifest(self):
        """Writes the manifest file containing the list of installed packages.

        :raise IOError: when the manifest file can't be written
        """
        pkg_list = [pkg.__dict__ for _, pkg in self.installed_pkgs.items()]
        data = {'manifest_version': 0, 'scriptdir': self.scriptdir,
                'plugindir': self.plugindir, 'installed_packages': pkg_list}

        with open(self.manifest, 'w') as f:
            json.dump(data, f)

    def bropath(self):
        return os.path.dirname(self.scriptdir)

    def bro_plugin_path(self):
        return os.path.dirname(self.plugindir)

    def add_source(self, name, git_url):
        """Add a git repository that acts as a source of packages.

        Returns True if the source is successfully added.  It may fail to be
        added if the git URL is invalid or if a source with a different git URL
        already exists with the same name.
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

    def default_source(self):
        return self.sources['default']

    def source_packages(self):
        """Return a list of `Package`s contained in all sources."""
        rval = []

        for _, source in self.sources.items():
            rval += source.packages()

        return rval

    def installed_packages(self):
        """Return a list of `Package`s that have been installed."""
        return [pkg for _, pkg in self.installed_pkgs.items()]

    def loaded_packages(self):
        """Return a list of `Package`s that have been loaded."""
        return [pkg for _, pkg in self.loaded_pkgs.items()]

    def package_build_log(self, pkg_path):
        """Return the path to the package manager's build log for a package."""
        name = Package.name_from_path(pkg_path)
        return os.path.join(self.package_clonedir, '.build-{}.log'.format(name))

    def match_source_packages(self, pkg_path):
        """Return a list of `Package`s that match a given path."""
        rval = []

        for pkg in self.source_packages():
            if pkg.matches_path(pkg_path):
                rval.append(pkg)

        return rval

    def find_installed_package(self, pkg_path):
        """Return a `Package` if one is installed that matches the name.

        A package's "name" is the last component of it's git URL.
        """
        pkg_name = Package.name_from_path(pkg_path)
        return self.installed_pkgs.get(pkg_name)

    def refresh(self):
        """Fetch latest git versions for sources and installed packages.

        This retrieves information about new packages and new versions of
        existing packages, but does not yet upgrade installed packaged.

        """
        for name, source in self.sources.items():
            LOG.debug('refresh "%s": pulling %s', name, source.git_url)
            source.clone.remote().pull()

        # @todo: `git fetch` for each installed package

    def remove(self, pkg_path):
        """Remove an installed package.

        Returns True if an installed package was removed.

        :raise IOError: if the package manifest file can't be written
        :raise OSError: if the installed package's directory can't be deleted
        """
        LOG.debug('removing "%s"', pkg_path)
        pkg_path = remove_trailing_slashes(pkg_path)
        pkg_to_remove = self.find_installed_package(pkg_path)

        if not pkg_to_remove:
            LOG.info('removing "%s": could not find matching package', pkg_path)
            return False

        self.unload(pkg_path)

        delete_path(os.path.join(self.package_clonedir, pkg_to_remove.name))
        delete_path(os.path.join(self.scriptdir, pkg_to_remove.name))
        delete_path(os.path.join(self.plugindir, pkg_to_remove.name))
        delete_path(os.path.join(self.bropath(), pkg_to_remove.name))

        del self.installed_pkgs[pkg_to_remove.name]
        self._write_manifest()

        # @todo: check dependencies
        LOG.debug('removed "%s"', pkg_path)
        return True

    def load(self, pkg_path):
        """Mark an installed package as being 'loaded'.

        The collection of 'loaded' packages is a convenient way for Bro to more
        simply load a whole group of packages installed via the package manager.

        Returns True if a package is successfully marked as loaded.

        :raise IOError: if the __load__.bro loader script can't be updated
        """
        LOG.debug('loading "%s"', pkg_path)
        pkg_path = remove_trailing_slashes(pkg_path)
        pkg_to_load = self.find_installed_package(pkg_path)

        if not pkg_to_load:
            LOG.info('loading "%s": no matching package', pkg_path)
            return False

        if self.loaded_pkgs.get(pkg_to_load.name):
            LOG.debug('loading "%s": already loaded', pkg_path)
            return True

        with open(self.autoload_script, 'a') as f:
            f.write('@load ./{}\n'.format(pkg_to_load.name))

        self.loaded_pkgs[pkg_to_load.name] = pkg_to_load
        LOG.debug('loaded "%s"', pkg_path)
        return True

    def unload(self, pkg_path):
        """Unmark an installed package as being 'loaded'.

        The collection of 'loaded' packages is a convenient way for Bro to more
        simply load a whole group of packages installed via the package manager.

        Returns True if a package is successfully unmarked as loaded.

        :raise IOError: if __load__.bro loader script cannot be updated
        """
        LOG.debug('unloading "%s"', pkg_path)
        pkg_path = remove_trailing_slashes(pkg_path)
        pkg_to_unload = self.find_installed_package(pkg_path)

        if not pkg_to_unload:
            LOG.info('unloading "%s": no matching package', pkg_path)
            return False

        if not self.loaded_pkgs.get(pkg_to_unload.name):
            LOG.debug('unloading "%s": already unloaded', pkg_path)
            return True

        with open(self.autoload_script, 'w') as f:
            content = ""

            for pkg_name in self.loaded_pkgs:
                if pkg_name != pkg_to_unload.name:
                    content += '@load ./{}\n'.format(pkg_name)

            f.write(content)

        del self.loaded_pkgs[pkg_to_unload.name]
        LOG.debug('unloaded "%s"', pkg_path)
        return True

    def install(self, pkg_path):
        """Install a package.

        Return empty string if package installation succeeded else an error
        string explaining why it failed.

        :raise IOError: if a state file can't be written
        """
        LOG.debug('installing "%s"', pkg_path)
        pkg_path = remove_trailing_slashes(pkg_path)
        conflict = self.find_installed_package(pkg_path)

        if conflict:
            if str(conflict).endswith(pkg_path):
                LOG.debug('installing "%s": already installed: %s',
                          pkg_path, conflict)
                return ''

            LOG.info('installing "%s": matched already installed package: %s',
                     pkg_path, conflict)
            return 'package with name "{}" ({}) is already installed'.format(
                conflict.name, conflict)

        matches = self.match_source_packages(pkg_path)

        if not matches:
            try:
                return self._install_from_git_url(pkg_path)
            except git.exc.GitCommandError as error:
                LOG.info('installing "%s": invalid git repo path: %s', pkg_path,
                         error)

            LOG.info('installing "%s": matched no source package', pkg_path)
            return 'package not found in sources and also not a valid git URL'

        if len(matches) > 1:
            matches_string = [str(match) for match in matches]
            LOG.info('installing "%s": matched multiple packages: %s',
                     pkg_path, matches_string)
            return str.format('"{}" matches multiple packages, try a more'
                              ' specific name from: {}',
                              pkg_path, matches_string)

        try:
            return self._install_package(matches[0])
        except git.exc.GitCommandError as error:
            LOG.warning('installing "%s": source package git repo is invalid',
                        pkg_path)
            return 'failed to clone package "{}": {}'.format(pkg_path, error)

        # @todo: install dependencies
        return ''

    def _install_package(self, package):
        """Install a `Package`.

        Return empty string if package installation succeeded else an error
        string explaining why it failed.

        :raise git.exc.GitCommandError: if the git repo is invalid
        :raise IOError: if the package manifest file can't be written
        """
        pkg_path = os.path.join(self.package_clonedir, package.name)
        delete_path(pkg_path)
        git.Repo.clone_from(package.git_url, pkg_path)

        default_metadata = {'bro_dist': self.bro_dist, 'scriptpath': '',
                            'pluginpath': 'build', 'buildcmd': ''}
        parser = configparser.SafeConfigParser(defaults=default_metadata)
        metadata_file = os.path.join(pkg_path, self.pkg_metadata_filename)

        if not parser.read(metadata_file):
            LOG.warning('installing "%s": no metadata file', package)
            return 'missing pkg.meta metadata file'

        if not parser.has_section('package'):
            LOG.warning('installing "%s": metadata missing [package]', package)
            return 'pkg.meta metadata file is missing [package] section'

        metadata = {item[0]: item[1] for item in parser.items('package')}
        package.metadata = metadata

        buildcmd = metadata['buildcmd']

        if buildcmd:
            LOG.debug('installing "%s": running buildcmd: %s',
                      package, buildcmd)
            build = subprocess.Popen(buildcmd,
                                     shell=True, cwd=pkg_path, bufsize=1,
                                     stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE)

            try:
                buildlog = self.package_build_log(pkg_path)

                with open(buildlog, 'w') as f:
                    LOG.warning('installing "%s": writing build log: %s',
                                package, buildlog)

                    f.write('=== STDERR ===\n')

                    for line in build.stderr:
                        f.write(line)

                    f.write('=== STDOUT ===\n')

                    for line in build.stdout:
                        f.write(line)

            except EnvironmentError as error:
                LOG.warning(
                    'installing "%s": failed to write build log %s %s: %s',
                    package, buildlog, error.errno, error.strerror)

            returncode = build.wait()

            if returncode != 0:
                return 'package buildcmd failed, see log in {}'.format(buildlog)

        scriptpath_src = os.path.join(self.package_clonedir, package.name,
                                      metadata['scriptpath'])
        scriptpath_dst = os.path.join(self.scriptdir, package.name)
        error = Manager._copy_package_dir(package, 'scriptpath',
                                          scriptpath_src, scriptpath_dst)
        make_symlink(os.path.join('packages', package.name),
                     os.path.join(self.bropath(), package.name))

        if error:
            return error

        pluginpath_src = os.path.join(self.package_clonedir, package.name,
                                      metadata['pluginpath'])
        pluginpath_dst = os.path.join(self.plugindir, package.name)
        error = Manager._copy_package_dir(package, 'pluginpath',
                                          pluginpath_src, pluginpath_dst)

        if error:
            return error

        self.installed_pkgs[package.name] = package
        self._write_manifest()
        LOG.debug('installed "%s"', pkg_path)
        return ''

    @staticmethod
    def _copy_package_dir(package, dirname, src, dst):
        """Copy a directory from a package to its installation location.

        Return empty string if package dir copy succeeded else an error string
        explaining why it failed.
        """
        try:
            if os.path.exists(src):
                copy_over_path(src, dst)
            else:
                LOG.info('installing "%s": nonexistant %s: %s',
                         package, dirname, src)
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

    def _install_from_git_url(self, git_url):
        """Install a package from a git URL.

        Return empty string if package installation succeeded else an error
        string explaining why it failed.

        :raise git.exc.GitCommandError: if the git repo is invalid
        :raise IOError: if the package manifest file can't be written
        """
        package = Package(git_url=git_url)
        return self._install_package(package)
