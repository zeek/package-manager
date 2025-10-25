"""
A module containing the definition of a "package source": a git repository
containing a collection of :file:`zkg.index` (or legacy :file:`bro-pkg.index`)
files.  These are simple INI files that can describe many Zeek packages.  Each
section of the file names a Zeek package along with the git URL where it is
located and metadata tags that help classify/describe it.
"""

import configparser
import filecmp
import os
import shutil

import git

from . import LOG
from ._util import (
    configparser_section_dict,
    delete_path,
    git_checkout,
    git_clone,
    git_default_branch,
    git_pull,
    git_version_tags,
)
from .config import (
    CONFIG,
)
from .package import (
    Package,
    get_package_metadata,
    name_from_path,
    parse_package_metadata,
    pick_metadata_file,
)

#: The name of package index files.
INDEX_FILENAME = "zkg.index"
LEGACY_INDEX_FILENAME = "bro-pkg.index"
#: The name of the package source file where package metadata gets aggregated.
AGGREGATE_DATA_FILE = "aggregate.meta"


class AggregationResults:
    """The return value of a call to :meth:`.Source.aggregate()`.

    Attributes:
        error (str): an empty string if no overall error occurred,
            otherwise a description of what went wrong.

        package_issues (list of (str, str)): a list of reasons for
            failing to collect metadata per packages/repository.
            The first tuple element gives the repository URL in which
            the problem occurred and the second tuple element describes
            the failure.
    """

    def __init__(
        self,
        error: str = "",
        package_issues: list[tuple[str, str]] | None = None,
    ) -> None:
        self.error = error
        self.package_issues = package_issues if package_issues else []


class Source:
    """A Zeek package source.

    This class contains properties of a package source like its name, remote git
    URL, and local git clone.

    Attributes:
        name (str): The name of the source as given by a config file key
            in it's ``[sources]`` section.

        git_url (str): The git URL of the package source.

        clone (git.Repo): The local git clone of the package source.
    """

    def __init__(
        self,
        name: str,
        clone_path: str,
        git_url: str,
        version: str | None = None,
    ) -> None:
        """Create a package source.

        Raises:
            git.GitCommandError: if the git repo is invalid
            OSError: if the git repo is invalid and can't be re-initialized
        """
        git_url = os.path.expanduser(git_url)
        self.name = name
        self.git_url = git_url
        self.clone: git.Repo

        try:
            self.clone = git.Repo(clone_path)
        except git.NoSuchPathError:
            LOG.debug('creating source clone of "%s" at %s', name, clone_path)
            self.clone = git_clone(git_url, clone_path, shallow=True)
        except git.InvalidGitRepositoryError:
            LOG.debug('deleting invalid source clone of "%s" at %s', name, clone_path)
            shutil.rmtree(clone_path)
            self.clone = git_clone(git_url, clone_path, shallow=True)
        else:
            LOG.debug('found source clone of "%s" at %s', name, clone_path)
            old_url = self.clone.git.config("--local", "--get", "remote.origin.url")

            if git_url != old_url:
                LOG.debug(
                    'url of source "%s" changed from %s to %s, reclone at %s',
                    name,
                    old_url,
                    git_url,
                    clone_path,
                )
                shutil.rmtree(clone_path)
                self.clone = git_clone(git_url, clone_path, shallow=True)

        git_checkout(self.clone, version or git_default_branch(self.clone))

        self.aggregate_file = os.path.join(self.clone.working_dir, AGGREGATE_DATA_FILE)

    def __str__(self) -> str:
        return self.git_url

    def __repr__(self) -> str:
        return self.git_url

    def package_index_files(self) -> list[str]:
        """Return a list of paths to package index files in the source."""
        rval = []
        visited_dirs = set()

        for root, dirs, files in os.walk(self.clone.working_dir, followlinks=True):
            stat = os.stat(root)
            visited_dirs.add((stat.st_dev, stat.st_ino))
            dirs_to_visit_next = []

            for d in dirs:
                stat = os.stat(os.path.join(root, d))

                if (stat.st_dev, stat.st_ino) not in visited_dirs:
                    dirs_to_visit_next.append(d)

            dirs[:] = dirs_to_visit_next

            try:
                dirs.remove(".git")
            except ValueError:
                pass

            for filename in files:
                if filename in {INDEX_FILENAME, LEGACY_INDEX_FILENAME}:
                    rval.append(os.path.join(root, filename))

        return sorted(rval)

    def packages(self) -> list[Package]:
        """Return a list of :class:`.package.Package` in the source."""
        rval = []
        # Use raw parser so no value interpolation takes place.
        parser = configparser.RawConfigParser()
        aggregate_file = os.path.join(self.clone.working_dir, AGGREGATE_DATA_FILE)
        parser.read(aggregate_file)

        for index_file in self.package_index_files():
            relative_path = index_file[len(str(self.clone.working_dir)) + 1 :]
            directory = os.path.dirname(relative_path)
            lines = []

            with open(index_file) as f:
                lines = [line.rstrip("\n") for line in f]

            for url in lines:
                pkg_name = name_from_path(url)
                agg_key = os.path.join(directory, pkg_name)
                metadata = {}

                if parser.has_section(agg_key):
                    metadata = dict(parser.items(agg_key))

                package = Package(
                    git_url=url,
                    source=self.name,
                    directory=directory,
                    metadata=metadata,
                )
                rval.append(package)

        return rval

    def refresh(self) -> str:
        LOG.debug('refresh "%s": pulling %s', self.name, self.git_url)

        agg_file_ours = os.path.join(CONFIG.scratch_dir(), AGGREGATE_DATA_FILE)
        agg_file_their_orig = agg_file_ours + ".orig"

        delete_path(agg_file_ours)
        delete_path(agg_file_their_orig)

        if os.path.isfile(self.aggregate_file):
            shutil.copy2(self.aggregate_file, agg_file_ours)

        self.clone.git.reset(hard=True)
        self.clone.git.clean("-f", "-x", "-d")

        if os.path.isfile(self.aggregate_file):
            shutil.copy2(self.aggregate_file, agg_file_their_orig)

        try:
            self.clone.git.fetch("--recurse-submodules=yes")
            git_pull(self.clone)
        except git.GitCommandError as error:
            LOG.error("failed to pull source %s: %s", self.name, error)
            return f"failed to pull from remote source: {error}"

        if os.path.isfile(agg_file_ours):
            if os.path.isfile(self.aggregate_file):
                # There's a tracked version of the file after pull.
                if os.path.isfile(agg_file_their_orig):
                    # We had local modifications to the file.
                    if filecmp.cmp(self.aggregate_file, agg_file_their_orig):
                        # Their file hasn't changed, use ours.
                        shutil.copy2(agg_file_ours, self.aggregate_file)
                        LOG.debug(
                            "aggregate file in source unchanged, restore local one",
                        )
                    else:
                        # Their file changed, use theirs.
                        LOG.debug("aggregate file in source changed, discard local one")
                else:
                    # File was untracked before pull and tracked after,
                    # use their version.
                    LOG.debug("new aggregate file in source, discard local one")
            else:
                # They don't have the file after pulling, so restore ours.
                shutil.copy2(agg_file_ours, self.aggregate_file)
                LOG.debug("no aggregate file in source, restore local one")

        return ""

    def aggregate(self, push: bool = False) -> AggregationResults:
        parser = configparser.ConfigParser(interpolation=None)
        prev_parser = configparser.ConfigParser(interpolation=None)
        prev_packages = set()

        if os.path.isfile(self.aggregate_file):
            prev_parser.read(self.aggregate_file)
            prev_packages = set(prev_parser.sections())

        aggregation_issues = []
        agg_adds, agg_mods, agg_dels = [], [], []

        for index_file in self.package_index_files():
            urls = []

            with open(index_file) as f:
                urls = [line.rstrip("\n") for line in f]

            for url in urls:
                pkg_name = name_from_path(url)
                clonepath = os.path.join(CONFIG.scratch_dir(), pkg_name)
                delete_path(clonepath)

                try:
                    clone = git_clone(url, clonepath, shallow=True)
                except git.GitCommandError as error:
                    LOG.warning(
                        "failed to clone %s, skipping aggregation: %s",
                        url,
                        error,
                    )
                    aggregation_issues.append((url, repr(error)))
                    continue

                version_tags = git_version_tags(clone)

                if len(version_tags):
                    version = version_tags[-1]
                else:
                    version = git_default_branch(clone)

                try:
                    git_checkout(clone, version)
                except git.GitCommandError as error:
                    LOG.warning(
                        'failed to checkout branch/version "%s" of %s, '
                        "skipping aggregation: %s",
                        version,
                        url,
                        error,
                    )
                    msg = f'failed to checkout branch/version "{version}": {error!r}'
                    aggregation_issues.append((url, msg))
                    continue

                metadata_file = pick_metadata_file(str(clone.working_dir))
                metadata_parser = configparser.ConfigParser(interpolation=None)
                invalid_reason = parse_package_metadata(
                    metadata_parser,
                    metadata_file,
                )

                if invalid_reason:
                    LOG.warning(
                        "skipping aggregation of %s: bad metadata: %s",
                        url,
                        invalid_reason,
                    )
                    aggregation_issues.append((url, invalid_reason))
                    continue

                metadata = get_package_metadata(metadata_parser)
                index_dir = os.path.dirname(index_file)[
                    len(CONFIG.sources_clone_dir()) + len(self.name) + 2 :
                ]
                qualified_name = os.path.join(index_dir, pkg_name)

                parser.add_section(qualified_name)

                for key, value in sorted(metadata.items()):
                    parser.set(qualified_name, key, value)

                parser.set(qualified_name, "url", url)
                parser.set(qualified_name, "version", version)

                if qualified_name not in prev_packages:
                    agg_adds.append(qualified_name)
                else:
                    prev_meta = configparser_section_dict(
                        prev_parser,
                        qualified_name,
                    )
                    new_meta = configparser_section_dict(parser, qualified_name)
                    if prev_meta != new_meta:
                        agg_mods.append(qualified_name)

        with open(self.aggregate_file, "w") as f:
            parser.write(f)

        agg_dels = list(prev_packages.difference(set(parser.sections())))

        adds_str = " (" + ", ".join(sorted(agg_adds)) + ")" if agg_adds else ""
        mods_str = " (" + ", ".join(sorted(agg_mods)) + ")" if agg_mods else ""
        dels_str = " (" + ", ".join(sorted(agg_dels)) + ")" if agg_dels else ""

        LOG.debug(
            "metadata refresh: %d additions%s, %d changes%s, %d removals%s",
            len(agg_adds),
            adds_str,
            len(agg_mods),
            mods_str,
            len(agg_dels),
            dels_str,
        )

        if push:
            if os.path.isfile(
                os.path.join(self.clone.working_dir, AGGREGATE_DATA_FILE),
            ):
                self.clone.git.add(AGGREGATE_DATA_FILE)

            if self.clone.is_dirty():
                # There's an assumption here that the dirty state is
                # due to a metadata refresh. This could be incorrect
                # if somebody makes local modifications and then runs
                # the refresh without --aggregate, but it's not clear
                # why one would use zkg for this as opposed to git
                # itself.
                self.clone.git.commit(
                    "--no-verify",
                    "--message",
                    "Update aggregated metadata.",
                )
                LOG.info('committed package source "%s" metadata update', self.name)

            self.clone.git.push("--no-verify")

        return AggregationResults("", aggregation_issues)
