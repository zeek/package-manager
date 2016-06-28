import sys
import os
import json
import shutil
import git

if sys.version_info[0] < 3:
    import ConfigParser as configparser
else:
    import configparser

from .util import (
    make_dir,
    remove_trailing_slashes,
    delete_path,
    make_symlink,
)
from .error import Error
from .source import Source
from .package import Package
from . import (
    __version__,
    LOG,
)


class Manager(object):

    def __init__(self, state_path):
        """Create package manager.

        :raise OSError: when a package manager state directory can't be created
        :raise IOError: when a package manager state file can't be created
        """
        LOG.debug('init Manager version %s', __version__)
        self.sources = {}
        self.installed_pkgs = {}
        self.loaded_pkgs = {}
        self.state_path = state_path
        self.state_path_sources = os.path.join(self.state_path, 'sources')
        self.state_path_packages = os.path.join(self.state_path, 'packages')
        self.state_path_bropath = os.path.join(self.state_path, 'bropath')
        self.state_path_pluginpath = os.path.join(
            self.state_path, 'pluginpath')
        self.state_path_manifest = os.path.join(
            self.state_path, 'manifest.json')
        self.state_path_loader_script = os.path.join(self.state_path_bropath,
                                                     'bro-pkg.bro')
        self.pkg_metadata_filename = 'pkg.meta'
        make_dir(self.state_path)
        make_dir(self.state_path_sources)
        make_dir(self.state_path_packages)
        make_dir(self.state_path_bropath)
        make_dir(self.state_path_pluginpath)

        if not os.path.exists(self.state_path_manifest):
            self._write_manifest()

        self._read_manifest()

        with open(self.state_path_loader_script, 'a+') as file:
            file.seek(0)
            loaded_pkg_names = [line.split()[1][2:] for line in file]
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
                file.truncate(0)
                content = ""

                for pkg_name in new_loaded_pkg_names:
                    content += '@load ./{}\n'.format(pkg_name)

                file.write(content)

    def _read_manifest(self):
        """Read the manifest file containing the list of installed packages.

        :raise IOError: when the manifest file can't be read
        """
        with open(self.state_path_manifest, 'r') as file:
            data = json.load(file)
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

    def _write_manifest(self):
        """Writes the manifest file containing the list of installed packages.

        :raise IOError: when the manifest file can't be written
        """
        pkg_list = [pkg.__dict__ for _, pkg in self.installed_pkgs.items()]
        data = {'manifest_version': 0, 'installed_packages': pkg_list}

        with open(self.state_path_manifest, 'w') as file:
            json.dump(data, file)

    def add_source(self, name, git_url):
        """Add a git repository that acts as a source of packages.

        :raise git.exc.GitCommandError: if the git repo is invalid
        """
        if name in self.sources:
            existing_source = self.sources[name]

            if existing_source.git_url == git_url:
                LOG.debug('duplicate source "%s"', name)
                return Error.NONE

            LOG.debug('duplicate source "%s" with conflicting URL', name)
            return Error.CONFLICTING_SOURCE

        clone_path = os.path.join(self.state_path_sources, name)

        try:
            source = Source(name=name, clone_path=clone_path, git_url=git_url)
        except git.exc.GitCommandError:
            return Error.INVALID_SOURCE
        else:
            self.sources[name] = source

        return Error.NONE

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

    def bropaths(self):
        """Return set of paths for use in BROPATH.

        Users should add these paths to the BROPATH environment variable in
        order to use installed packages with Bro.
        """
        return {self.state_path_bropath}

    def pluginpaths(self):
        """Return set of paths for use in BRO_PLUGIN_PATH.

        Users should add these paths to the BRO_PLUGIN_PATH environment variable
        in order to use installed packages with Bro.
        """
        return {self.state_path_pluginpath}

    def match_source_packages(self, pkg_path):
        """Return a list of `Package`s that match a given path."""
        rval = []

        for pkg in self.source_packages():
            if pkg.matches_path(pkg_path):
                rval.append(pkg)

        return rval

    def match_installed_packages(self, pkg_path):
        """Return a `Package` if one is installed that matches the name.

        A package's "name" is the last component of it's git URL.
        """
        pkg_name = Package.name_from_path(pkg_path)
        rval = self.installed_pkgs.get(pkg_name)

        if not rval:
            return None

        if rval.matches_path(pkg_path):
            return rval

        return None

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

        :raise IOError: if the package manifest file can't be written
        :raise OSError: if the installed package's directory can't be deleted
        """
        LOG.debug('removing "%s"', pkg_path)
        pkg_path = remove_trailing_slashes(pkg_path)
        pkg_to_remove = self.match_installed_packages(pkg_path)

        if not pkg_to_remove:
            LOG.info('removing "%s": could not find matching package', pkg_path)
            return Error.INVALID_PACKAGE

        self.unload(pkg_path)

        delete_path(os.path.join(self.state_path_packages, pkg_to_remove.name))
        delete_path(os.path.join(self.state_path_bropath, pkg_to_remove.name))
        delete_path(os.path.join(
            self.state_path_pluginpath, pkg_to_remove.name))

        del self.installed_pkgs[pkg_to_remove.name]
        self._write_manifest()

        # @todo: check dependencies
        LOG.debug('removed "%s"', pkg_path)
        return Error.NONE

    def load(self, pkg_path):
        """Mark an installed package as being 'loaded'.

        The collection of 'loaded' packages is a convenient way for Bro to more
        simply load a whole group of packages installed via the package manager.

        :raise IOError: if the bro-pkg.bro file can't be updated
        """
        LOG.debug('loading "%s"', pkg_path)
        pkg_path = remove_trailing_slashes(pkg_path)
        pkg_to_load = self.match_installed_packages(pkg_path)

        if not pkg_to_load:
            LOG.info('loading "%s": no matching package', pkg_path)
            return Error.INVALID_PACKAGE

        if self.loaded_pkgs.get(pkg_to_load.name):
            LOG.debug('loading "%s": already loaded', pkg_path)
            return Error.NONE

        with open(self.state_path_loader_script, 'a') as file:
            file.write('@load ./{}\n'.format(pkg_to_load.name))

        self.loaded_pkgs[pkg_to_load.name] = pkg_to_load
        LOG.debug('loaded "%s"', pkg_path)
        return Error.NONE

    def unload(self, pkg_path):
        """Unmark an installed package as being 'loaded'.

        The collection of 'loaded' packages is a convenient way for Bro to more
        simply load a whole group of packages installed via the package manager.

        :raise IOError: if bro-pkg.bro loader script cannot be updated
        """
        LOG.debug('unloading "%s"', pkg_path)
        pkg_path = remove_trailing_slashes(pkg_path)
        pkg_to_unload = self.match_installed_packages(pkg_path)

        if not pkg_to_unload:
            LOG.info('unloading "%s": no matching package', pkg_path)
            return Error.INVALID_PACKAGE

        if not self.loaded_pkgs.get(pkg_to_unload.name):
            LOG.debug('unloading "%s": already unloaded', pkg_path)
            return Error.NONE

        with open(self.state_path_loader_script, 'w') as file:
            content = ""

            for pkg_name in self.loaded_pkgs:
                if pkg_name != pkg_to_unload.name:
                    content += '@load ./{}\n'.format(pkg_name)

            file.write(content)

        del self.loaded_pkgs[pkg_to_unload.name]
        LOG.debug('unloaded "%s"', pkg_path)
        return Error.NONE

    def install(self, pkg_path):
        """Install a package.

        :raise IOError: if a state file can't be written
        """
        LOG.debug('installing "%s"', pkg_path)
        pkg_path = remove_trailing_slashes(pkg_path)
        conflict = self.match_installed_packages(pkg_path)

        if conflict:
            if str(conflict).endswith(pkg_path):
                LOG.debug('installing "%s": already installed: %s',
                          pkg_path, conflict.name)
                return Error.NONE

            LOG.info('installing "%s": matched already installed package: %s',
                     pkg_path, conflict.name)
            return Error.CONFLICTING_PACKAGE

        matches = self.match_source_packages(pkg_path)

        if not matches:
            try:
                rval = self._install_from_git_url(pkg_path)
            except git.exc.GitCommandError:
                LOG.info('installing "%s": invalid git repo path', pkg_path)
                return Error.INVALID_PACKAGE
            else:
                return rval

            LOG.info('installing "%s": matched no source package', pkg_path)
            return Error.INVALID_PACKAGE

        if len(matches) > 1:
            LOG.info('installing "%s": matched multiple packages: %s',
                     pkg_path, [str(match) for match in matches])
            return Error.AMBIGUOUS_PACKAGE

        try:
            rval = self._install_package(matches[0])
        except git.exc.GitCommandError:
            LOG.warning('installing "%s": source package git repo is invalid',
                        pkg_path)
            return Error.INVALID_PACKAGE
        else:
            return rval

        # @todo: run the package's build command
        # @todo: install dependencies
        return Error.NONE

    def _install_package(self, package):
        """Install a `Package`.

        :raise git.exc.GitCommandError: if the git repo is invalid
        :raise IOError: if the package manifest file can't be written
        """
        pkg_path = os.path.join(self.state_path_packages, package.name)
        delete_path(pkg_path)
        git.Repo.clone_from(package.git_url, pkg_path)

        default_metadata = {'bropath': '',
                            'pluginpath': 'build', 'buildcmd': ''}
        parser = configparser.SafeConfigParser(defaults=default_metadata)
        metadata_file = os.path.join(pkg_path, self.pkg_metadata_filename)

        if not parser.read(metadata_file):
            LOG.warning('installing "%s": no metadata file', package)
            return Error.INVALID_PACKAGE_METADATA

        if not parser.has_section('package'):
            LOG.warning('installing "%s": invalid metadata format', package)
            return Error.INVALID_PACKAGE_METADATA

        metadata = {item[0]: item[1] for item in parser.items('package')}
        package.metadata = metadata

        bropath_link_path = os.path.join(self.state_path_bropath, package.name)
        bropath_link_target = os.path.join('..', 'packages', package.name,
                                           metadata['bropath'])
        make_symlink(bropath_link_target, bropath_link_path)
        pluginpath_link_path = os.path.join(self.state_path_pluginpath,
                                            package.name)
        pluginpath_link_target = os.path.join('..', 'packages', package.name,
                                              metadata['pluginpath'])
        make_symlink(pluginpath_link_target, pluginpath_link_path)

        self.installed_pkgs[package.name] = package
        self._write_manifest()
        LOG.debug('installed "%s"', pkg_path)
        return Error.NONE

    def _install_from_git_url(self, git_url):
        """Install a package from a git URL.

        :raise git.exc.GitCommandError: if the git repo is invalid
        :raise IOError: if the package manifest file can't be written
        """
        package = Package(git_url=git_url)
        return self._install_package(package)
