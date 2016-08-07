"""
A module with various data structures used for interacting with and querying
the properties and status of Bro packages.
"""


from ._util import (
    remove_trailing_slashes
)


class InstalledPackage(object):
    """An installed package and its current status.

    Attributes:
        package (:class:`Package`): the installed package

        status (:class:`PackageStatus`): the status of the installed package
    """

    def __init__(self, package, status):
        self.package = package
        self.status = status

    def __lt__(self, other):
        return str(self.package) < str(other.package)


class PackageStatus(object):
    """The status of an installed package.

    This class contains properties of a package related to how the package
    manager will operate on it.

    Attributes:
        is_loaded (bool): whether a package is marked as "loaded".

        is_pinned (bool): whether a package is allowed to be upgraded.

        is_outdated (bool): whether a newer version of the package exists.

        tracking_method (str): either "branch" or "version" to indicate
            whether package upgrades should stick to a git branch or use git
            version tags.

        current_version (str): the current version of the installed
            package, which is either a git branch name or a git version tag.

        current_hash (str): the git sha1 hash associated with installed
            package's current version/commit.
    """

    def __init__(self, is_loaded=False, is_pinned=False, is_outdated=False,
                 tracking_method=None, current_version=None, current_hash=None):
        self.is_loaded = is_loaded
        self.is_pinned = is_pinned
        self.is_outdated = is_outdated
        self.tracking_method = tracking_method
        self.current_version = current_version
        self.current_hash = current_hash


class PackageInfo(object):
    """Contains information on an arbitrary package.

    If the package is installed, then its status is also available.

    Attributes:
        package (:class:`Package`): the relevant Bro package

        status (:class:`PackageStatus`): this attribute is set for installed
            packages

        invalid_reason (str): this attribute is set when there is a problem
            with gathering package information and explains what went wrong
    """

    def __init__(self, package=None, status=None,
                 invalid_reason=''):
        self.package = package
        self.status = status
        self.invalid_reason = invalid_reason


class Package(object):
    """A Bro package.

    This class contains properties of a package that are defined by the package
    itself, like its metadata, version, URL, and name.

    Attributes:
        git_url (str): the git URL which uniquely identifies where the
            Bro package is located

        name (str): the canonical name of the package, which is always the
            last component of the git URL path

        source (str): the package source this package comes from, which
            may be empty if the package is not a part of a source (i.e. the user
            is referring directly to the package's git URL).

        module_dir (str): the directory within the package source where
            this package is located as a submodule.  E.g. if the package source
            has a git submodule at "alice/foo" for the package named "foo", then
            `module_dir` should be "alice".  It may also be empty if the package
            is not part of a package source.

        metadata (dict of str -> str): the contents of the package's
            :file:`bro-pkg.meta` file

        versions (list of str): a list of the package's availabe git version
            tags
    """

    @classmethod
    def name_from_path(cls, path):
        return remove_trailing_slashes(path).split('/')[-1]

    def __init__(self, git_url, source='', module_dir='', metadata=None,
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

    def module_path(self):
        """Return the package's git submodule path within its package source.

        E.g. for a package source with a git submodule at "alice/foo" for
        a package named "foo", this method returns "alice/foo".
        If the package has no source, then just the package name is returned.
        """
        if self.module_dir:
            return '{}/{}'.format(self.module_dir, self.name)

        return self.name

    def qualified_name(self):
        """Return the shortest name that qualifies/distinguishes the package.

        If the package is part of a source, then this returns
        "source_name/:meth:`module_path()`", else the package's git URL is
        returned.
        """
        if self.source:
            return '{}/{}'.format(self.source, self.module_path())

        return self.git_url

    def matches_path(self, path):
        """Return whether this package has a matching path/name.

        E.g for a package with :meth:`qualified_name()` of "bro/alice/foo",
        the following inputs will match: "foo", "alice/foo", "bro/alice/foo"
        """
        path_parts = path.split('/')

        if self.source:
            pkg_path = self.qualified_name()
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
