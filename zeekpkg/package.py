"""
A module with various data structures used for interacting with and querying
the properties and status of Zeek packages.
"""

import os
import re
from functools import total_ordering

import semantic_version as semver

from ._util import find_sentence_end, normalize_version_tag
from .uservar import UserVar

#: The name of files used by packages to store their metadata.
METADATA_FILENAME = "zkg.meta"

TRACKING_METHOD_VERSION = "version"
TRACKING_METHOD_BRANCH = "branch"
TRACKING_METHOD_COMMIT = "commit"
TRACKING_METHOD_BUILTIN = "builtin"

BUILTIN_SOURCE = "zeek-builtin"
BUILTIN_SCHEME = "zeek-builtin://"

PLUGIN_MAGIC_FILE = "__zeek_plugin__"
PLUGIN_MAGIC_FILE_DISABLED = "__zeek_plugin__.disabled"


def name_from_path(path: str) -> str:
    """Returns the name of a package given a path to its git repository."""
    return canonical_url(path).split("/")[-1]


def canonical_url(path: str) -> str:
    """Returns the url of a package given a path to its git repo."""
    url = path.rstrip("/")

    if url.startswith(".") or url.startswith("/"):
        url = os.path.realpath(url)

    return url


def is_valid_name(name: str) -> bool:
    """Returns True if name is a valid package name, else False."""
    if name != name.strip():
        # Reject names with leading/trailing whitespace
        return False

    # For aliases: Do not allow file separators.
    if "/" in name:
        return False

    # Avoid creating hidden files and directories.
    if name.startswith("."):
        return False

    if name in ("package", "packages"):
        return False

    return True


def aliases(metadata_dict: dict[str, str]) -> list[str]:
    """Return a list of package aliases found in metadata's 'aliases' field."""
    if "aliases" not in metadata_dict:
        return []

    return re.split(r",\s*|\s+", metadata_dict["aliases"])


def tags(metadata_dict: dict[str, str]) -> list[str]:
    """Return a list of tag strings found in the metadata's 'tags' field."""
    if "tags" not in metadata_dict:
        return []

    return re.split(r",\s*", metadata_dict["tags"])


def short_description(metadata_dict: dict[str, str]) -> str:
    """Returns the first sentence of the metadata's 'desciption' field."""
    if "description" not in metadata_dict:
        return ""

    description = metadata_dict["description"]
    lines = description.split("\n")
    rval = ""

    for line in lines:
        line = line.lstrip()
        rval += " "
        period_idx = find_sentence_end(line)

        if period_idx == -1:
            rval += line
        else:
            rval += line[: period_idx + 1]
            break

    return rval.lstrip()


def user_vars(
    metadata_dict: dict[str, str],
) -> list[tuple[str, str | None, str | None]] | None:
    """Returns a list of (str, str, str) from metadata's 'user_vars' field.

    Each entry in the returned list is a the name of a variable, its value,
    and its description.

    If the 'user_vars' field is not present, an empty list is returned.  If it
    is malformed, then None is returned.
    """
    uvars = UserVar.parse_dict(metadata_dict)

    if uvars is None:
        return None

    return [(uvar.name(), uvar.val(), uvar.desc()) for uvar in uvars]


def dependencies(
    metadata_dict: dict[str, str],
    field: str = "depends",
) -> dict[str, str] | None:
    """Returns a dictionary of (str, str) based on metadata's dependency field.

    The keys indicate the name of a package (shorthand name or full git URL).
    The names 'zeek' or 'zkg' may also be keys that indicate a dependency on a
    particular Zeek or zkg version.

    The values indicate a semantic version requirement.

    If the dependency field is malformed (e.g. number of keys not equal to
    number of values), then None is returned.
    """
    if field not in metadata_dict:
        return {}

    rval = {}
    depends = metadata_dict[field]
    parts = depends.split()
    keys = parts[::2]
    values = parts[1::2]

    if len(keys) != len(values):
        return None

    for i, k in enumerate(keys):
        if i < len(values):
            rval[k] = values[i]

    return rval


class PackageVersion:
    """
    Helper class to compare package versions with version specs.
    """

    def __init__(self, method: str | None, version: str | None) -> None:
        self.method = method
        self.version = version
        self.req_semver = None

    def fullfills(self, version_spec: str) -> tuple[str, bool]:
        """
        Whether this package version fullfills the given version_spec.

        Returns:
            (some message, bool)
        """
        if version_spec == "*":  # anything goes
            return "", True

        if self.method == TRACKING_METHOD_COMMIT:
            return f'tracking method commit not compatible with "{version_spec}"', False

        if self.method == TRACKING_METHOD_BRANCH:
            return "tracking method branch and commit", False

        # TRACKING_METHOD_BRANCH / TRACKING_METHOD_BUILTIN
        if version_spec.startswith("branch="):
            branch = version_spec[len("branch=") :]
            return (
                f"branch {branch} requested, but using method {self.method}",
                False,
            )

        if self.req_semver is None:
            assert self.version
            normal_version = normalize_version_tag(self.version)
            self.req_semver = semver.Version.coerce(normal_version)

        try:
            semver_spec = semver.Spec(version_spec)
        except ValueError:
            return f'invalid semver spec: "{version_spec}"', False
        else:
            if self.req_semver in semver_spec:
                return "", True

            return f"{self.version} not in {version_spec}", False


@total_ordering
class InstalledPackage:
    """An installed package and its current status.

    Attributes:
        package (:class:`Package`): the installed package

        status (:class:`PackageStatus`): the status of the installed package
    """

    def __init__(self, package: "Package", status: "PackageStatus") -> None:
        self.package = package
        self.status = status

    def __repr__(self) -> str:
        return f"InstalledPackage(package={self.package!r}, status={self.status!r})"

    def __hash__(self) -> int:
        return hash(str(self.package))

    def __eq__(self, other: object) -> bool:
        return isinstance(other, InstalledPackage) and str(self.package) == str(
            other.package,
        )

    def __lt__(self, other: object) -> bool:
        return isinstance(other, InstalledPackage) and (self.package) < str(
            other.package,
        )

    def is_builtin(self) -> bool:
        return self.package.is_builtin()

    def fullfills(self, version_spec: str) -> tuple[str, bool]:
        """
        Does the current version fullfill version_spec?
        """
        return PackageVersion(
            self.status.tracking_method,
            self.status.current_version,
        ).fullfills(version_spec)


class PackageStatus:
    """The status of an installed package.

    This class contains properties of a package related to how the package
    manager will operate on it.

    Attributes:
        is_loaded (bool): whether a package is marked as "loaded".

        is_pinned (bool): whether a package is allowed to be upgraded.

        is_outdated (bool): whether a newer version of the package exists.

        tracking_method (str): either "branch", "version", "commit", or
            "builtin" to indicate (respectively) whether package upgrades
            should stick to a git branch, use git version tags, do nothing
            because the package is to always use a specific git commit hash,
            or do nothing because the package is built into Zeek.

        current_version (str): the current version of the installed
            package, which is either a git branch name or a git version tag.

        current_hash (str): the git sha1 hash associated with installed
            package's current version/commit.
    """

    def __init__(
        self,
        is_loaded: bool = False,
        is_pinned: bool = False,
        is_outdated: bool = False,
        tracking_method: str | None = None,
        current_version: str | None = None,
        current_hash: str | None = None,
    ) -> None:
        self.is_loaded = is_loaded
        self.is_pinned = is_pinned
        self.is_outdated = is_outdated
        self.tracking_method = tracking_method
        self.current_version = current_version
        self.current_hash = current_hash

    def __repr__(self) -> str:
        member_str = ", ".join(f"{k}={v!r}" for (k, v) in self.__dict__.items())
        return f"PackageStatus({member_str})"


class PackageInfo:
    """Contains information on an arbitrary package.

    If the package is installed, then its status is also available.

    Attributes:
        package (:class:`Package`): the relevant Zeek package

        status (:class:`PackageStatus`): this attribute is set for installed
            packages

        metadata (dict of str -> str): the contents of the package's
            :file:`zkg.meta`

        versions (list of str): a list of the package's availabe git version
            tags

        metadata_version: the package version that the metadata is from

        version_type: either 'version', 'branch', or 'commit' to
            indicate whether the package info/metadata was taken from a release
            version tag, a branch, or a specific commit hash.

        invalid_reason (str): this attribute is set when there is a problem
            with gathering package information and explains what went wrong.

        metadata_file: the absolute path to the :file:`zkg.meta` for this package.
            Use this if you'd like to parse the metadata yourself. May not be
            defined, in which case the value is None.
    """

    def __init__(
        self,
        package: "Package",
        status: PackageStatus | None = None,
        metadata: dict[str, str] | None = None,
        versions: list[str] | None = None,
        metadata_version: str | None = "",
        invalid_reason: str = "",
        version_type: str = "",
        metadata_file: str | None = None,
        default_branch: str | None = None,
    ) -> None:
        self.package = package
        self.status = status
        self.metadata = {} if metadata is None else metadata
        self.versions = [] if versions is None else versions
        self.metadata_version = metadata_version
        self.version_type = version_type
        self.invalid_reason = invalid_reason
        self.metadata_file = metadata_file
        self.default_branch = default_branch

    def aliases(self) -> list[str]:
        """Return a list of package name aliases.

        The canonical one is listed first.
        """
        return aliases(self.metadata)

    def tags(self) -> list[str]:
        """Return a list of keyword tags associated with the package.

        This will be the contents of the package's `tags` field."""
        return tags(self.metadata)

    def short_description(self) -> str:
        """Return a short description of the package.

        This will be the first sentence of the package's 'description' field."""
        return short_description(self.metadata)

    def dependencies(self, field: str = "depends") -> dict[str, str] | None:
        """Returns a dictionary of dependency -> version strings.

        The keys indicate the name of a package (shorthand name or full git
        URL).  The names 'zeek' or 'zkg' may also be keys that indicate a
        dependency on a particular Zeek or zkg version.

        The values indicate a semantic version requirement.

        If the dependency field is malformed (e.g. number of keys not equal to
        number of values), then None is returned.
        """
        return dependencies(self.metadata, field)

    def user_vars(self) -> list[UserVar] | None:
        """Returns a list of user variables parsed from metadata's 'user_vars' field.

        If the 'user_vars' field is not present, an empty list is returned.  If
        it is malformed, then None is returned.

        Returns:
            list of zeekpkg.uservar.UserVar, or None on error
        """
        return UserVar.parse_dict(self.metadata)

    def best_version(self) -> str:
        """Returns the best/latest version of the package that is available.

        If the package has any git release tags, this returns the highest one,
        else it returns the default branch like 'main' or 'master'.
        """
        if self.versions:
            return self.versions[-1]

        assert self.default_branch
        return self.default_branch

    def is_builtin(self) -> bool:
        if self.package:
            return self.package.is_builtin()

        return False

    def __repr__(self) -> str:
        return f"PackageInfo(package={self.package!r}, status={self.status!r})"


@total_ordering
class Package:
    """A Zeek package.

    This class contains properties of a package that are defined by the package
    git repository itself and the package source it came from.

    Attributes:
        git_url (str): the git URL which uniquely identifies where the
            Zeek package is located

        name (str): the canonical name of the package, which is always the
            last component of the git URL path

        source (str): the package source this package comes from, which
            may be empty if the package is not a part of a source (i.e. the user
            is referring directly to the package's git URL).

        directory (str): the directory within the package source where the
            :file:`zkg.index` containing this package is located.
            E.g. if the package source has a package named "foo" declared in
            :file:`alice/zkg.index`, then `dir` is equal to "alice".
            It may also be empty if the package is not part of a package source
            or if it's located in a top-level :file:`zkg.index` file.

        metadata (dict of str -> str): the contents of the package's
            :file:`zkg.meta`. If the package has not been installed then this
            information may come from the last aggregation of the source's
            :file:`aggregate.meta` file (it may not be accurate/up-to-date).
    """

    def __init__(
        self,
        git_url: str,
        source: str = "",
        directory: str = "",
        metadata: dict[str, str] | None = None,
        name: str | None = None,
        canonical: bool = False,
    ) -> None:
        self.git_url = git_url
        self.source = source
        self.directory = directory
        self.metadata = {} if metadata is None else metadata
        self.name: str

        if name:
            self.name = name

        if not canonical:
            self.git_url = canonical_url(git_url)

            if not source and os.path.exists(git_url):
                # Ensures getting real path of relative directories.
                # e.g. canonical_url catches "./foo" but not "foo"
                self.git_url = os.path.realpath(self.git_url)

            self.name = name_from_path(git_url)

    def __str__(self) -> str:
        return self.qualified_name()

    def __repr__(self) -> str:
        return (
            f"Package(git_url={self.git_url!r}, source={self.source!r},"
            f" directory={self.directory!r} name={self.name!r})"
        )

    def is_builtin(self) -> bool:
        return self.git_url.startswith(BUILTIN_SCHEME)

    def __hash__(self) -> int:
        return hash(str(self))

    def __eq__(self, other: object) -> bool:
        return str(self) == str(other)

    def __lt__(self, other: object) -> bool:
        return str(self) < str(other)

    def aliases(self) -> list[str]:
        """Return a list of package name aliases.

        The canonical one is listed first.
        """
        return aliases(self.metadata)

    def tags(self) -> list[str]:
        """Return a list of keyword tags associated with the package.

        This will be the contents of the package's `tags` field and may
        return results from the source's aggregated metadata if the package
        has not been installed yet."""
        return tags(self.metadata)

    def short_description(self) -> str:
        """Return a short description of the package.

        This will be the first sentence of the package's 'description' field
        and may return results from the source's aggregated metadata if the
        package has not been installed yet."""
        return short_description(self.metadata)

    def dependencies(self, field: str = "depends") -> dict[str, str] | None:
        """Returns a dictionary of dependency -> version strings.

        The keys indicate the name of a package (shorthand name or full git
        URL).  The names 'zeek' or 'zkg' may also be keys that indicate a
        dependency on a particular Zeek or zkg version.

        The values indicate a semantic version requirement.

        If the dependency field is malformed (e.g. number of keys not equal to
        number of values), then None is returned.
        """
        return dependencies(self.metadata, field)

    def user_vars(self) -> list[tuple[str, str | None, str | None]] | None:
        """Returns a list of (str, str, str) from metadata's 'user_vars' field.

        Each entry in the returned list is a the name of a variable, it's value,
        and its description.

        If the 'user_vars' field is not present, an empty list is returned.  If
        it is malformed, then None is returned.
        """
        return user_vars(self.metadata)

    def name_with_source_directory(self) -> str | None:
        """Return the package's name within its package source.

        E.g. for a package source with a package named "foo" in
        :file:`alice/zkg.index`, this method returns "alice/foo".
        If the package has no source or sub-directory within the source, then
        just the package name is returned.
        """
        if self.directory:
            return f"{self.directory}/{self.name}"

        return self.name

    def qualified_name(self) -> str:
        """Return the shortest name that qualifies/distinguishes the package.

        If the package is part of a source, then this returns
        "source_name/:meth:`name_with_source_directory()`", else the package's
        git URL is returned.
        """
        if self.source:
            return f"{self.source}/{self.name_with_source_directory()}"

        return self.git_url

    def matches_path(self, path: str) -> bool:
        """Return whether this package has a matching path/name.

        E.g for a package with :meth:`qualified_name()` of "zeek/alice/foo",
        the following inputs will match: "foo", "alice/foo", "zeek/alice/foo"
        """
        path_parts = path.split("/")

        if self.source:
            pkg_path = self.qualified_name()
            pkg_path_parts = pkg_path.split("/")

            for i, part in reversed(list(enumerate(path_parts))):
                ri = i - len(path_parts)

                if part != pkg_path_parts[ri]:
                    return False

            return True

        if len(path_parts) == 1 and path_parts[-1] == self.name:
            return True

        return path == self.git_url


def make_builtin_package(
    *,
    name: str,
    current_version: str,
    current_hash: str | None = None,
) -> PackageInfo:
    """
    Given ``name``, ``version`` and ``commit`` as found in Zeek's ``zkg.provides``
    entry, construct a :class:`PackageInfo` instance representing the built-in
    package.
    """
    package = Package(
        git_url=f"{BUILTIN_SCHEME}{name}",
        name=name,
        source=BUILTIN_SOURCE,
        canonical=True,
    )

    status = PackageStatus(
        is_loaded=True,  # May not hold in the future?
        is_outdated=False,
        is_pinned=True,
        tracking_method=TRACKING_METHOD_BUILTIN,
        current_version=current_version,
        current_hash=current_hash,
    )

    return PackageInfo(
        package=package,
        status=status,
        versions=[current_version],
    )
