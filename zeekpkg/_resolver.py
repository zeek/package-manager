"""nab-resolver integration for zkg dependency resolution.

Provides `_Node` (the per-package graph node), `_ZkgProvider` (the
`ResolverProvider` implementation), and the helpers they depend on.
`Manager.validate_dependencies` constructs a graph of `_Node` objects and
passes it to `_ZkgProvider`, which the nab-resolver `Resolver` then drives.
"""

from __future__ import annotations

import configparser
import os
from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING, cast

import git
import semantic_version as semver
from nab_resolver.errors import ResolutionError
from nab_resolver.ranges import Range
from nab_resolver.resolver import Resolver, ResolverProvider
from nab_resolver.types import Incompatibility, RangeProtocol
from typing_extensions import Self

from ._util import _semver_versions, git_version_tags, is_sha1
from .package import (
    LEGACY_METADATA_FILENAME,
    METADATA_FILENAME,
    PackageInfo,
    PackageVersion,
)
from .package import dependencies as pkg_dependencies

if TYPE_CHECKING:
    from .manager import Manager

__all__ = ["Range"]


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


def _constraint_to_range(constraint: str) -> _FmtRange:
    """Convert a normalized zkg constraint string to a nab-resolver `Range`."""
    if constraint in ("*", ""):
        return _FmtRange(Range.full()._intervals)
    result: _FmtRange = _FmtRange(Range.full()._intervals)
    clause = semver.SimpleSpec(_normalize_constraint(constraint)).clause
    matchers = list(clause.clauses) if hasattr(clause, "clauses") else [clause]
    for m in matchers:
        v = semver.Version.coerce(str(m.target))
        if m.operator == ">=":
            result = result & _FmtRange(Range.at_least(v)._intervals)
        elif m.operator == ">":
            result = result & _FmtRange(Range.greater_than(v)._intervals)
        elif m.operator == "<=":
            result = result & _FmtRange(Range.at_most(v)._intervals)
        elif m.operator == "<":
            result = result & _FmtRange(Range.less_than(v)._intervals)
        elif m.operator == "==":
            result = result & _FmtRange(Range.singleton(v)._intervals)
    return result


def _fmt_range(r: Range[semver.Version]) -> str:
    """Format a Range as a user-friendly constraint string.

    Replaces nab-resolver's default ``(-inf, X) | (X, +inf)`` notation with
    operator-prefixed semver strings such as ``<1.0.0 | >1.0.0``.
    """
    parts = []
    for lo, lo_inc, hi, hi_inc in r._intervals:
        lo_inf = not isinstance(lo, semver.Version)
        hi_inf = not isinstance(hi, semver.Version)
        if lo_inf and hi_inf:
            parts.append("*")
        elif lo_inf:
            parts.append(("<=" if hi_inc else "<") + str(hi))
        elif hi_inf:
            parts.append((">=" if lo_inc else ">") + str(lo))
        elif lo == hi and lo_inc and hi_inc:
            parts.append("=" + str(lo))
        else:
            parts.append(
                (">=" if lo_inc else ">")
                + str(lo)
                + ", "
                + ("<=" if hi_inc else "<")
                + str(hi),
            )
    return " | ".join(parts) if parts else "none"


class _FmtRange(Range[semver.Version]):
    """A `Range` whose `__str__` produces operator-prefixed semver notation.

    nab-resolver has no global range-formatting hook: `narrow_for_display`
    only covers terms that pass through the `_narrow_positive` path in the
    error reporter.  The `CONSTRAINT`-cause path in `_render_line` interpolates
    `incompatibility.constraint_range` directly, bypassing `narrow_for_display`
    entirely and exposing the raw ``(-inf, X) | (X, +inf)`` sentinel strings.

    Making every range we construct carry its own formatted `__str__` fixes all
    render sites at once without relying on any library hook.  The operator
    overrides are necessary because `Range.__and__`, `__or__`, and `__invert__`
    construct their results as plain `Range` objects; without overriding them,
    composed ranges lose the subclass and revert to the raw notation.
    """

    __slots__ = ()

    def __str__(self) -> str:
        return _fmt_range(self)

    @classmethod
    def empty(cls) -> Self:
        return cls(super().empty()._intervals)

    @classmethod
    def full(cls) -> Self:
        return cls(super().full()._intervals)

    @classmethod
    def singleton(cls, version: semver.Version) -> Self:
        return cls(super().singleton(version)._intervals)

    def __and__(self, other: object) -> Self:
        result = super().__and__(other)
        if not isinstance(result, Range):
            return result  # pragma: no cover
        return type(self)(result._intervals)

    def __or__(self, other: object) -> Self:
        result = super().__or__(other)
        if not isinstance(result, Range):
            return result  # pragma: no cover
        return type(self)(result._intervals)

    def __invert__(self) -> Self:
        return type(self)(super().__invert__()._intervals)

    def __sub__(self, other: object) -> Self:
        result = super().__sub__(other)
        if not isinstance(result, Range):
            return result  # pragma: no cover
        return type(self)(result._intervals)


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
            if not self._versions.get(qname) and node.info:
                # No git tags: try a concrete version from metadata, installed
                # state, or versions list; fall back to 0.0.0 so the solver can
                # still resolve packages with no semver release history.
                registered = False
                for raw in (
                    node.info.metadata_version,
                    node.installed_version.version if node.installed_version else None,
                    node.info.versions[-1] if node.info.versions else None,
                ):
                    if raw and not is_sha1(raw):
                        try:
                            self._versions[qname] = [semver.Version.coerce(raw)]
                            registered = True
                        except ValueError:
                            pass
                        break
                if not registered:
                    self._versions[qname] = [semver.Version("0.0.0")]

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

    def _qualify_deps(self, raw_deps: dict[str, str]) -> dict[str, str]:
        result: dict[str, str] = {}
        for dep, spec in raw_deps.items():
            if dep in ("zeek", "zkg") or spec.startswith("branch="):
                continue
            di = self._manager.find_builtin_package(dep)
            if di is None:
                di = self._manager.info(dep, prefer_installed=False)
            if di.invalid_reason:
                continue
            result[di.package.qualified_name()] = _normalize_constraint(spec)
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
            return (raw_tag, self._qualify_deps(raw_deps))
        clone_dir = os.path.dirname(node.info.metadata_file)
        try:
            clone = git.Repo(clone_dir)
        except git.InvalidGitRepositoryError:
            # Directory package -- no git history; use current metadata.
            raw_tag = node.info.metadata_version or node.info.best_version()
            raw_deps = node.info.dependencies(field="depends") or {}
            return (raw_tag, self._qualify_deps(raw_deps))
        found_tag: str | None = None
        for rt, nv in _semver_versions(git_version_tags(clone)):
            if semver.Version.coerce(nv) == version:
                found_tag = rt
                break
        if found_tag is None:
            # Synthetic version (no matching tag) -- use current HEAD metadata.
            raw_deps = node.info.dependencies(field="depends") or {}
            raw_tag = node.info.metadata_version or node.info.best_version()
        else:
            raw_tag = found_tag
            raw_deps = _deps_at_version(clone, raw_tag)
        return (raw_tag, self._qualify_deps(raw_deps))

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
        r = cast(Range[semver.Version], constraint)
        return _FmtRange(r._intervals)


def _run_solver(
    provider: _ZkgProvider,
    requirements: Mapping[str, Range[semver.Version]],
    constraints: Mapping[str, Range[semver.Version]],
    graph: dict[str, _Node],
    requested_qnames: set[str],
    installed_qnames: set[str],
    branch_pkg_names: set[str],
    soft_pinned: dict[str, str],
    ignore_suggestions: bool,
    lookup_dep: Callable[[str], PackageInfo | None],
) -> tuple[str, list[tuple[str, str, bool]]]:
    """Run the nab-resolver and return a topo-sorted install list.

    Returns a ``(error, items)`` pair. On success ``error`` is empty and
    ``items`` is a list of ``(qname, raw_tag, is_suggestion)`` tuples in
    dependency order (dependees before dependers). On failure ``error`` is
    the first line of the resolver's error message and ``items`` is empty.

    ``lookup_dep`` resolves a short package name to its ``PackageInfo``; it
    replaces the ``find_builtin_package`` / ``_cached_info`` calls that
    previously lived in ``manager.py``.
    """
    resolver: Resolver[str, semver.Version] = Resolver(
        provider,
        range_type=Range,
        root_version=semver.Version("0.0.0"),
    )
    try:
        resolved: dict[str, semver.Version] = resolver.resolve(
            requirements,
            constraints=constraints,
        )
    except ResolutionError as e:
        return (str(e), [])

    suggestion_names: set[str] = {
        name for name, node in graph.items() if node.is_suggestion
    }

    def _pkg_deps(qn: str) -> list[str]:
        result_d: list[str] = []
        rv = resolved.get(qn)
        if rv is not None:
            cache_entry = provider._cache.get((qn, rv))
            if cache_entry:
                _, d = cache_entry
                result_d = list(d)
                if not ignore_suggestions:
                    nd = graph.get(qn)
                    if nd and nd.info:
                        raw_sug = nd.info.dependencies(field="suggests") or {}
                        for dep_s in raw_sug:
                            if dep_s in ("zeek", "zkg"):
                                continue
                            di = lookup_dep(dep_s)
                            if di is not None and not di.invalid_reason:
                                dqn = di.package.qualified_name()
                                if dqn not in result_d:
                                    result_d.append(dqn)
                return sorted(result_d)
        nd = graph.get(qn)
        if nd and nd.info:
            raw: dict[str, str] = nd.info.dependencies(field="depends") or {}
            if not ignore_suggestions:
                raw = {**raw, **(nd.info.dependencies(field="suggests") or {})}
            for dep_s in raw:
                if dep_s in ("zeek", "zkg"):
                    continue
                di = lookup_dep(dep_s)
                if di is not None and not di.invalid_reason:
                    result_d.append(di.package.qualified_name())
        return sorted(result_d)

    dfs_visited: set[str] = set()
    dfs_in_stack: set[str] = set()

    def _dfs_emit(start: str) -> list[tuple[str, str, bool]]:
        result: list[tuple[str, str, bool]] = []
        stack: list[tuple[str, bool]] = [(start, False)]
        while stack:
            qn, post = stack.pop()
            if post:
                dfs_in_stack.discard(qn)
                is_upgraded = (
                    qn in soft_pinned
                    and qn in resolved
                    and _is_versioned_package(soft_pinned[qn])
                    and resolved[qn] > semver.Version.coerce(soft_pinned[qn])
                )
                if (
                    qn in requested_qnames
                    or (qn in installed_qnames and not is_upgraded)
                    or qn in branch_pkg_names
                ):
                    continue
                node = graph.get(qn)
                if node is None or node.info is None:
                    continue
                is_sug = qn in suggestion_names
                rv = resolved.get(qn)
                if rv is not None:
                    cache_entry = provider._cache.get((qn, rv))
                    raw_tag = (
                        cache_entry[0] if cache_entry else node.info.best_version()
                    )
                else:
                    raw_tag = node.info.best_version()
                result.append((qn, raw_tag, is_sug))
            else:
                if qn in dfs_visited or qn in dfs_in_stack:
                    continue
                dfs_visited.add(qn)
                dfs_in_stack.add(qn)
                stack.append((qn, True))
                for dep_qn in reversed(_pkg_deps(qn)):
                    if dep_qn not in dfs_visited and dep_qn not in dfs_in_stack:
                        stack.append((dep_qn, False))
        return result

    seeds = list(requested_qnames) + list(branch_pkg_names)
    post_order: list[tuple[str, str, bool]] = []
    for seed in seeds:
        post_order.extend(_dfs_emit(seed))

    # post_order is leaves-first; reverse to get root-first for the return
    # value (caller reverses again when installing, so leaves end up first).
    seen_res: set[str] = set()
    res: list[tuple[str, str, bool]] = []
    for qn, raw_tag, is_sug in reversed(post_order):
        if qn not in seen_res:
            seen_res.add(qn)
            if graph.get(qn) is not None and graph[qn].info is not None:
                res.append((qn, raw_tag, is_sug))

    return ("", res)


def _is_versioned_package(v: str) -> bool:
    """Return True if *v* is a semver-coercible version the solver can use."""
    if is_sha1(v):
        return False
    try:
        semver.Version.coerce(v)
        return True
    except ValueError:
        return False


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
