"""
A module with various data structures used for interacting with and querying
the properties and status of Bro packages.
"""

import os

from ._util import (
    remove_trailing_slashes
)

#: The name of files used by packages to store their metadata.
METADATA_FILENAME = 'bro-pkg.meta'


def name_from_path(path):
    """Returns the name of a package given a path to its git repository."""
    return canonical_url(path).split('/')[-1]


def canonical_url(path):
    """Returns the url of a package given a path to its git repo."""
    url = remove_trailing_slashes(path)

    if url.startswith('.') or url.startswith('/'):
        url = os.path.realpath(url)

    return url


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

        metadata (dict of str -> str): the contents of the package's
            :file:`bro-pkg.meta` file

        versions (list of str): a list of the package's availabe git version
            tags

        metadata_version: the package version that the metadata is from

        invalid_reason (str): this attribute is set when there is a problem
            with gathering package information and explains what went wrong
    """

    def __init__(self, package=None, status=None, metadata=None, versions=None,
                 metadata_version='', invalid_reason=''):
        self.package = package
        self.status = status
        self.metadata = {} if metadata is None else metadata
        self.versions = [] if versions is None else versions
        self.metadata_version = metadata_version
        self.invalid_reason = invalid_reason


class Package(object):
    """A Bro package.

    This class contains properties of a package that are defined by the package
    git repository itself and the package source it came from.

    Attributes:
        git_url (str): the git URL which uniquely identifies where the
            Bro package is located

        name (str): the canonical name of the package, which is always the
            last component of the git URL path

        source (str): the package source this package comes from, which
            may be empty if the package is not a part of a source (i.e. the user
            is referring directly to the package's git URL).

        directory (str): the directory within the package source where the
            :file:`bro-pkg.index` containing this package is located.
            E.g. if the package source has a package named "foo" declared in
            :file:`alice/bro-pkg.index`, then `dir` is equal to "alice".
            It may also be empty if the package is not part of a package source
            or if it's located in a top-level :file:`bro-pkg.index` file.

        index_data (dict of str -> str): the data from the package's
            :data:`.source.INDEX_FILENAME`.
    """

    def __init__(self, git_url, source='', directory='', index_data=None):
        self.git_url = canonical_url(git_url)
        self.name = name_from_path(git_url)
        self.source = source
        self.directory = directory
        self.index_data = {} if index_data is None else index_data

    def __str__(self):
        return self.qualified_name()

    def __repr__(self):
        return self.git_url

    def __lt__(self, other):
        return str(self) < str(other)

    def tags(self):
        """Return a list of tags in the package's `index_data` attribute."""
        if 'tags' not in self.index_data:
            return []

        import re
        return re.split(',\s*', self.index_data['tags'])

    def name_with_source_directory(self):
        """Return the package's within its package source.

        E.g. for a package source with a package named "foo" in
        :file:`alice/bro-pkg.index`, this method returns "alice/foo".
        If the package has no source or sub-directory within the source, then
        just the package name is returned.
        """
        if self.directory:
            return '{}/{}'.format(self.directory, self.name)

        return self.name

    def qualified_name(self):
        """Return the shortest name that qualifies/distinguishes the package.

        If the package is part of a source, then this returns
        "source_name/:meth:`name_with_source_directory()`", else the package's
        git URL is returned.
        """
        if self.source:
            return '{}/{}'.format(self.source,
                                  self.name_with_source_directory())

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
