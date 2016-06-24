import os
import json
import shutil
import git

from .util import (
    make_dir,
    remove_trailing_slash,
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
        self.state_path = state_path
        self.state_path_sources = os.path.join(self.state_path, 'sources')
        self.state_path_packages = os.path.join(self.state_path, 'packages')
        self.state_path_manifest = os.path.join(
            self.state_path, 'manifest.json')
        make_dir(self.state_path)
        make_dir(self.state_path_sources)
        make_dir(self.state_path_packages)

        if not os.path.exists(self.state_path_manifest):
            self._store_manifest()

        self._load_manifest()

    def _load_manifest(self):
        """Loads the manifest file containing the list of installed packages.

        :raise IOError: when the manifest file can't be read
        """
        with open(self.state_path_manifest, 'r') as f:
            data = json.load(f)
            pkg_list = data['installed_packages']
            self.installed_pkgs = {}

            for pkg_dict in pkg_list:
                pkg_name = pkg_dict['name']
                pkg_git_url = pkg_dict['git_url']
                pkg_source = pkg_dict['source']
                pkg_author = pkg_dict['author']
                pkg = Package(git_url=pkg_git_url, source=pkg_source,
                              author=pkg_author, name=pkg_name)
                self.installed_pkgs[pkg_name] = pkg

    def _store_manifest(self):
        """Writes the manifest file containing the list of installed packages.

        :raise IOError: when the manifest file can't be written
        """
        pkg_list = [pkg.__dict__ for _, pkg in self.installed_pkgs.items()]
        data = {'manifest_version': 0, 'installed_packages': pkg_list}

        with open(self.state_path_manifest, 'w') as f:
            json.dump(data, f)

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
        pkg_name = pkg_path.split('/')[-1]
        return self.installed_pkgs.get(pkg_name)

    def refresh(self):
        """Fetch latest git versions for sources and installed packages.

        This retrieves information about new packages and new versions of
        existing packages, but does not yet upgrade installed packaged.

        """
        for name, source in self.sources.items():
            LOG.debug('refresh "%s": pulling %s', name, source.git_url)
            source.clone.remote().pull()

        # @todo: `git pull` in the source package for each installed package

    def list(self, category):
        """Return a list of `Package`s that match a given category."""
        if category == 'all':
            return self.source_packages()

        if category == 'installed':
            return self.installed_packages()

        raise NotImplementedError
        # @todo: handle other categories

    def remove(self, pkg_path):
        """Remove an installed package.

        :raise IOError: if the package manifest file can't be written
        :raise OSError: if the installed package's directory can't be deleted
        """
        LOG.debug('removing "%s"', pkg_path)
        pkg_path = remove_trailing_slash(pkg_path)
        pkg_to_remove = self.match_installed_packages(pkg_path)

        if not pkg_to_remove:
            LOG.info('removing "%s": could not find matching package', pkg_path)
            return Error.INVALID_PACKAGE

        local_path = os.path.join(self.state_path_packages, pkg_to_remove.name)
        del self.installed_pkgs[pkg_to_remove.name]
        self._store_manifest()
        shutil.rmtree(local_path)

        # @todo: check dependencies
        # @todo: unload package
        LOG.debug('removed "%s"', pkg_path)
        return Error.NONE

    def install(self, pkg_path):
        """Install a package.

        :raise IOError: if the package manifest file can't be written
        """
        LOG.debug('installing "%s"', pkg_path)
        pkg_path = remove_trailing_slash(pkg_path)
        conflict = self.match_installed_packages(pkg_path)

        if conflict:
            LOG.info('installing "%s": matched already installed package: %s',
                     pkg_path, conflict.name)
            return Error.CONFLICTING_PACKAGE

        matches = self.match_source_packages(pkg_path)

        if not matches:
            try:
                self._install_from_git_url(pkg_path)
            except git.exc.GitCommandError:
                LOG.info('installing "%s": invalid git repo path', pkg_path)
                return Error.INVALID_PACKAGE
            else:
                LOG.debug('installed "%s"', pkg_path)
                return Error.NONE

            LOG.info('installing "%s": matched no source package', pkg_path)
            return Error.INVALID_PACKAGE

        if len(matches) > 1:
            LOG.info('installing "%s": matched multiple packages: %s',
                     pkg_path, [str(match) for match in matches])
            return Error.AMBIGUOUS_PACKAGE

        try:
            self._install_package(matches[0])
        except git.exc.GitCommandError:
            LOG.warn('installing "%s": source package git repo is invalid',
                     pkg_path)
            return Error.INVALID_PACKAGE

        LOG.debug('installed "%s"', pkg_path)
        # @todo: install dependencies
        # @todo: load the package
        return Error.NONE

    def _install_package(self, package):
        """Install a `Package`.

        :raise git.exc.GitCommandError: if the git repo is invalid
        :raise IOError: if the package manifest file can't be written
        """
        pkg_path = os.path.join(self.state_path_packages, package.name)
        git.Repo.clone_from(package.git_url, pkg_path)
        self.installed_pkgs[package.name] = package
        self._store_manifest()

    def _install_from_git_url(self, git_url):
        """Install a package from a git URL.

        :raise git.exc.GitCommandError: if the git repo is invalid
        :raise IOError: if the package manifest file can't be written
        """
        package = Package(git_url=git_url)
        self._install_package(package)
