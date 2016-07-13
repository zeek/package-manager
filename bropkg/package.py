from .util import (
    remove_trailing_slashes
)


class InstalledPackage(object):

    def __init__(self, package, status):
        self.package = package
        self.status = status

    def __lt__(self, other):
        return str(self.package) < str(other.package)


class PackageStatus(object):

    def __init__(self, is_loaded=False, is_pinned=False, is_outdated=False,
                 tracking_method=None, current_version=None, current_hash=None):
        self.is_loaded = is_loaded
        self.is_pinned = is_pinned
        self.is_outdated = is_outdated
        self.tracking_method = tracking_method
        self.current_version = current_version
        self.current_hash = current_hash


class PackageInfo(object):

    def __init__(self, package=None, status=None,
                 invalid_reason=''):
        self.package = package
        self.status = status
        self.invalid_reason = invalid_reason


class Package(object):

    @classmethod
    def name_from_path(cls, path):
        return remove_trailing_slashes(path).split('/')[-1]

    def __init__(self, git_url, source=None, module_dir=None, metadata=None,
                 versions=None):
        self.git_url = remove_trailing_slashes(git_url)
        self.name = Package.name_from_path(git_url)
        self.source = source
        self.module_dir = module_dir
        self.metadata = {} if metadata is None else metadata
        self.versions = [] if versions is None else versions

    def __str__(self):
        return self.qualified_name()

    def __repr__(self):
        return self.git_url

    def __lt__(self, other):
        return str(self) < str(other)

    def qualified_name(self):
        if self.source:
            if self.module_dir:
                return '{}/{}/{}'.format(self.source, self.module_dir,
                                         self.name)
            else:
                return '{}/{}'.format(self.source, self.name)
        else:
            return self.git_url

    def matches_path(self, path):
        """Return whether this package has a matching path/name."""
        path_parts = path.split('/')

        if self.source:
            pkg_path = str(self)
            pkg_path_parts = pkg_path.split('/')

            for i, part in reversed(list(enumerate(path_parts))):
                ri = i - len(path_parts)

                if part != pkg_path_parts[ri]:
                    return False

            return True
        else:
            if len(path_parts) == 1 and path_parts[-1] == self.name:
                return True

            return path == self.git_url
