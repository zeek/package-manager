import logging
import os
import errno
import shutil
import git

LOG = logging.getLogger(__name__)
LOG.addHandler(logging.NullHandler())


def version_string():
    # @todo: autogenerate this
    return '0.1'


def make_dir(path):
    """Create a directory or do nothing if it already exists.

    :raise OSError: if directory cannot be created
    """
    try:
        os.makedirs(path)
    except OSError as exception:
        if exception.errno != errno.EEXIST:
            raise
        elif os.path.isfile(path):
            raise


class Error(object):
    NONE = 0
    INVALID_PACKAGE = 1
    AMBIGUOUS_PACKAGE = 2
    CONFLICTING_PACKAGE = 3
    INVALID_SOURCE = 4
    CONFLICTING_SOURCE = 5


class Source(object):

    def __init__(self, name, clone_path, git_url):
        """Create a package source.

        :raise git.exc.GitCommandError: if the git repo is invalid
        """
        git_url = os.path.expanduser(git_url)
        self.name = name
        self.git_url = git_url

        try:
            self.clone = git.Repo(clone_path)
        except git.exc.NoSuchPathError:
            LOG.debug('creating source clone of "%s" at %s', name, clone_path)
            self.clone = git.Repo.clone_from(git_url, clone_path)
        except git.exc.InvalidGitRepositoryError:
            LOG.debug('deleting invalid source clone of "%s" at %s',
                      name, clone_path)
            shutil.rmtree(clone_path)
            self.clone = git.Repo.clone_from(git_url, clone_path)
        else:
            LOG.debug('found source clone of "%s" at %s', name, clone_path)
            old_urls = [url for url in self.clone.remote().urls]

            if git_url not in old_urls:
                LOG.debug(
                    'url of source "%s" changed from %s to %s, reclone at %s',
                    name, old_urls, git_url, clone_path)
                shutil.rmtree(clone_path)
                self.clone = git.Repo.clone_from(git_url, clone_path)

    def __str__(self):
        return self.git_url

    def __repr__(self):
        return self.git_url

    def packages(self):
        """Return list of `SourcePackage`s contained in source repository."""
        rval = []

        for submodule in self.clone.submodules:
            parts = submodule.name.split('/')

            if len(parts) < 2:
                pkg_author = None
                pkg_name = parts[0]
            else:
                pkg_author = parts[0]
                pkg_name = parts[1]

            rval.append(SourcePackage(submodule.url, source=self.name,
                                      author=pkg_author, name=pkg_name))

        return rval


class Package(object):

    def __init__(self, url, name=None):
        self.url = url
        self.name = name if name else url.split('/')[-1]

    def __str__(self):
        return self.name

    def __repr__(self):
        return self.url

    def __lt__(self, other):
        return str(self) < str(other)


class SourcePackage(Package):

    def __init__(self, url, source, author=None, name=None):
        super(SourcePackage, self).__init__(url, name)
        self.source = source
        self.author = author

    def __str__(self):
        if self.author:
            return "{}/{}/{}".format(self.source, self.author, self.name)
        else:
            return "{}/{}".format(self.source, self.name)

    def __repr__(self):
        return self.url

    def __lt__(self, other):
        return str(self) < str(other)

    def name_matches(self, name):
        """Return this package has a matching name."""
        if self.name == name:
            return True

        if self.author and "{}/{}".format(self.author, self.name) == name:
            return True

        if "{}/{}/{}".format(self.source, self.author, self.name) == name:
            return True

        return False


class Manager(object):

    def __init__(self, state_path):
        """Create package manager.

        :raise OSError: when a package manager state directory can't be created
        """
        LOG.debug('init Manager version %s', version_string())
        self.sources = {}
        self.state_path = state_path
        self.state_path_sources = os.path.join(self.state_path, 'sources')
        self.state_path_packages = os.path.join(self.state_path, 'packages')
        make_dir(self.state_path)
        make_dir(self.state_path_sources)
        make_dir(self.state_path_packages)

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
        """Return list of `SourcePackage`s contained in all sources."""
        rval = []

        for _, source in self.sources.items():
            rval += source.packages()

        return rval

    def match_package_name(self, package_name):
        """Return a list of `SourcePackage`s that match a given name."""
        rval = []

        for pkg in self.source_packages():
            if pkg.name_matches(package_name):
                rval.append(pkg)

        return rval

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
        if category == 'all':
            return self.source_packages()

        raise NotImplementedError
        # @todo: handle other categories

    def install(self, package_name):
        LOG.debug('installing "%s"', package_name)
        matches = self.match_package_name(package_name)

        if not matches:
            LOG.warn('installing "%s": matched no source package', package_name)
            # @todo: check if package is a valid local/remote path to a git repo
            return Error.INVALID_PACKAGE

        if len(matches) > 1:
            LOG.warn('installing "%s": matched multiple packages: %s',
                     package_name, [str(match) for match in matches])
            return Error.AMBIGUOUS_PACKAGE

        # @todo: find conflicting packages
        # @todo: install it

        LOG.debug('installed "%s"', package_name)
        return Error.NONE
