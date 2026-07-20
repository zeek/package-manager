"""nab-resolver integration for zkg dependency resolution.

Provides `_Node` (the per-package graph node), `_ZkgProvider` (the
`ResolverProvider` implementation), and the helpers they depend on.
`Manager.validate_dependencies` constructs a graph of `_Node` objects and
passes it to `_ZkgProvider`, which the nab-resolver `Resolver` then drives.
"""

from __future__ import annotations

import configparser
import os
from collections.abc import Mapping
from typing import TYPE_CHECKING

import git
import semantic_version as semver
from nab_resolver.ranges import Range
from nab_resolver.resolver import ResolverProvider
from nab_resolver.types import Incompatibility, RangeProtocol

from ._util import _semver_versions, git_version_tags
from .package import (
    LEGACY_METADATA_FILENAME,
    METADATA_FILENAME,
    PackageInfo,
    PackageVersion,
)
from .package import dependencies as pkg_dependencies

if TYPE_CHECKING:
    from .manager import Manager


class _Node:
    """Dependency graph node used inside `validate_dependencies`."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.info: PackageInfo | None = None
        self.requested_version: PackageVersion | None = None
        self.installed_version: PackageVersion | None = None
        self.dependers: dict[str, str] = {}
        self.dependees: dict[str, str] = {}
        self.is_suggestion = False

    def __str__(self) -> str:
        return (
            f"{self.name}\n\t"
            f"requested: {self.requested_version}\n\t"
            f"installed: {self.installed_version}\n\t"
            f"dependers: {self.dependers}\n\t"
            f"suggestion: {self.is_suggestion}"
        )


def _get_branch_names(clone: git.Repo) -> list[str]:
    rval = []
    for ref in clone.references:
        branch_name = str(ref.name)
        if not branch_name.startswith("origin/"):
            continue
        rval.append(branch_name.split("origin/")[1])
    return rval


def _normalize_constraint(spec: str) -> str:
    """Normalize bare `=X` to `==X` for semver compatibility."""
    if spec.startswith("=") and not spec.startswith("=="):
        return "=" + spec
    return spec


def _constraint_to_range(constraint: str) -> Range[semver.Version]:
    """Convert a normalized zkg constraint string to a nab-resolver `Range`."""
    if constraint in ("*", ""):
        return Range.full()
    result: Range[semver.Version] = Range.full()
    clause = semver.SimpleSpec(_normalize_constraint(constraint)).clause
    matchers = list(clause.clauses) if hasattr(clause, "clauses") else [clause]
    for m in matchers:
        v = semver.Version.coerce(str(m.target))
        if m.operator == ">=":
            result = result & Range.at_least(v)
        elif m.operator == ">":
            result = result & Range.greater_than(v)
        elif m.operator == "<=":
            result = result & Range.at_most(v)
        elif m.operator == "<":
            result = result & Range.less_than(v)
        elif m.operator == "==":
            result = result & Range.singleton(v)
    return result


class _ZkgProvider(ResolverProvider["str", "semver.Version"]):
    """nab-resolver `ResolverProvider` backed by zkg package metadata.

    Lazily fetches dependency information per (package, version) on demand
    from git repos using `_deps_at_version`, caching results for post-resolution
    use by `_pkg_deps` and the DFS topo sort.
    """

    def __init__(
        self,
        manager: Manager,
        graph: dict[str, _Node],
    ) -> None:
        self._manager = manager
        self._graph = graph
        self._versions: dict[str, list[semver.Version]] = {}
        self._cache: dict[tuple[str, semver.Version], tuple[str, dict[str, str]]] = {}

        for qname, node in graph.items():
            if node.info and node.info.metadata_file:
                clone_dir = os.path.dirname(node.info.metadata_file)
                try:
                    clone = git.Repo(clone_dir)
                    pairs = _semver_versions(git_version_tags(clone))
                    self._versions[qname] = [
                        semver.Version.coerce(nv) for _, nv in pairs
                    ]
                except Exception:
                    pass

    def choose_version(
        self,
        package: str,
        version_range: RangeProtocol[semver.Version],
    ) -> semver.Version | None:
        for v in reversed(self._versions.get(package, [])):
            if v in version_range:
                return v
        return None

    def has_satisfying_version(
        self,
        package: str,
        version_range: RangeProtocol[semver.Version],
    ) -> bool:
        return any(v in version_range for v in self._versions.get(package, []))

    def get_dependencies(
        self,
        package: str,
        version: semver.Version,
    ) -> dict[str, Range[semver.Version]]:
        key = (package, version)
        if key not in self._cache:
            self._cache[key] = self._fetch_deps(package, version)
        _, deps_str = self._cache[key]
        result: dict[str, Range[semver.Version]] = {}
        for dep, spec in deps_str.items():
            try:
                result[dep] = _constraint_to_range(spec)
            except ValueError:
                pass
        return result

    def _fetch_deps(
        self,
        qname: str,
        version: semver.Version,
    ) -> tuple[str, dict[str, str]]:
        node = self._graph.get(qname)
        if node is None or node.info is None:
            return (str(version), {})
        if not node.info.metadata_file:
            # Builtin / directory package -- use current metadata.
            raw_tag = node.info.metadata_version or node.info.best_version()
            raw_deps = node.info.dependencies(field="depends") or {}
            qualified: dict[str, str] = {}
            for dep, spec in raw_deps.items():
                if dep in ("zeek", "zkg") or spec.startswith("branch="):
                    continue
                di = self._manager.find_builtin_package(dep)
                if di is None:
                    di = self._manager.info(dep, prefer_installed=False)
                if di.invalid_reason:
                    continue
                qualified[di.package.qualified_name()] = _normalize_constraint(spec)
            return (raw_tag, qualified)
        clone_dir = os.path.dirname(node.info.metadata_file)
        clone = git.Repo(clone_dir)
        raw_tag = str(version)
        for rt, nv in _semver_versions(git_version_tags(clone)):
            if semver.Version.coerce(nv) == version:
                raw_tag = rt
                break
        raw_deps = _deps_at_version(clone, raw_tag)
        qualified_deps: dict[str, str] = {}
        for dep, spec in raw_deps.items():
            if dep in ("zeek", "zkg") or spec.startswith("branch="):
                continue
            di = self._manager.find_builtin_package(dep)
            if di is None:
                di = self._manager.info(dep, prefer_installed=False)
            if di.invalid_reason:
                continue
            qualified_deps[di.package.qualified_name()] = _normalize_constraint(spec)
        return (raw_tag, qualified_deps)

    def prioritize(
        self,
        package: str,
        version_range: RangeProtocol[semver.Version],
        conflict_counts: Mapping[str, int],
        culprit_counts: Mapping[str, int] | None = None,
    ) -> int:
        return -len(self._versions.get(package, []))

    def is_ready(self, package: str) -> bool:
        return True

    def receive_partial_solution_hint(
        self,
        positive_ranges: Mapping[str, RangeProtocol[semver.Version]],
        decisions: Mapping[str, semver.Version],
    ) -> None:
        pass

    def consume_pending_clauses(self) -> list[Incompatibility[str, semver.Version]]:
        return []

    def consume_force_backtrack_targets(self) -> list[str]:
        return []

    def widen_decision(
        self,
        package: str,
        version: semver.Version,
    ) -> RangeProtocol[semver.Version] | None:
        return None

    def narrow_for_display(
        self,
        package: str,
        constraint: RangeProtocol[semver.Version],
    ) -> RangeProtocol[semver.Version]:
        return constraint


def _deps_at_version(clone: git.Repo, tag: str) -> dict[str, str]:
    """Return the dependency dict for `clone` at `tag`.

    Reads `zkg.meta`, falling back to `bro-pkg.meta`. Returns `{}` if
    neither file exists at `tag` or the `depends` field is absent.
    """
    content: str | None = None
    for filename in (METADATA_FILENAME, LEGACY_METADATA_FILENAME):
        try:
            content = clone.git.show(f"{tag}:{filename}")
            break
        except git.GitCommandError:
            continue

    if content is None:
        return {}

    parser = configparser.ConfigParser()
    parser.read_string(content)
    meta = dict(parser["package"]) if parser.has_section("package") else {}
    return pkg_dependencies(meta, field="depends") or {}
