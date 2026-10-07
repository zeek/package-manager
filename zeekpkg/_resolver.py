"""nab-resolver integration for zkg dependency resolution.

Provides `_Solver` (the combined graph builder, `ResolverProvider`, and
topo-sort driver) and its helpers.  `Manager.validate_dependencies` delegates
to `_Solver.resolve`.
"""

from __future__ import annotations

import configparser
import copy
import os
from collections.abc import Mapping
from typing import TYPE_CHECKING, cast

import git
import semantic_version as semver
from nab_resolver.errors import ResolutionError
from nab_resolver.ranges import Range
from nab_resolver.resolver import BaseProvider, Resolver
from nab_resolver.types import RangeProtocol
from typing_extensions import Self

from . import LOG, __version__
from ._util import (
    _semver_versions,
    get_zeek_version,
    git_version_tags,
    is_sha1,
    normalize_version_tag,
)
from .package import (
    LEGACY_METADATA_FILENAME,
    METADATA_FILENAME,
    PackageInfo,
    PackageVersion,
    TrackingMethod,
    canonical_url,
    name_from_path,
)
from .package import dependencies as pkg_dependencies

if TYPE_CHECKING:
    from .manager import Manager

__all__ = ["Range"]


class _Node:
    """Dependency graph node used inside `_Solver`."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.info: PackageInfo | None = None
        self.requested_version: PackageVersion | None = None
        self.installed_version: PackageVersion | None = None
        self.is_suggestion = False

    def __str__(self) -> str:
        return (
            f"{self.name}\n\t"
            f"requested: {self.requested_version}\n\t"
            f"installed: {self.installed_version}\n\t"
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
    """Range subclass whose ``__str__`` uses operator-prefixed semver notation."""

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

    parser = configparser.ConfigParser(interpolation=None)
    parser.read_string(content)
    meta = dict(parser["package"]) if parser.has_section("package") else {}
    return pkg_dependencies(meta, field="depends") or {}


class _Solver(BaseProvider["str", "semver.Version"]):
    """Graph builder, nab-resolver provider, and topo-sort driver."""

    def __init__(
        self,
        manager: Manager,
        graph: dict[str, _Node] | None = None,
    ) -> None:
        self._manager = manager
        self._graph: dict[str, _Node] = graph if graph is not None else {}
        self._versions: dict[str, list[semver.Version]] = {}
        self._cache: dict[tuple[str, semver.Version], tuple[str, dict[str, str]]] = {}
        self._info_cache: dict[str, PackageInfo] = {}
        if graph is not None:
            self._discover_versions()

    def resolve(
        self,
        requested_packages: list[tuple[str, str]],
        ignore_installed: bool = False,
        ignore_suggestions: bool = False,
        use_builtins: bool = True,
    ) -> tuple[str, list[tuple[PackageInfo, str, bool]]]:
        requests: list[_Node] = []

        for name, version in requested_packages:
            info = self._manager.info(name, version=version, prefer_installed=False)
            if info.invalid_reason:
                return (f'invalid package "{name}": {info.invalid_reason}', [])
            node = _Node(info.package.qualified_name())
            node.info = info
            node.requested_version = PackageVersion(info.version_type, version)
            if err := self._add_node(node):
                return (err, [])
            requests.append(node)

        hard_pinned: dict[str, str] = {}
        soft_pinned: dict[str, str] = {}
        installed_qnames: set[str] = set()
        if not ignore_installed:
            hard_pinned, soft_pinned, installed_qnames = self._seed_installed(
                use_builtins,
            )

        if err := self._walk_deps(ignore_suggestions, use_builtins):
            return (err, [])

        self._discover_versions()

        branch_pkgs, err = self._collect_branches(ignore_suggestions)
        if err:
            return (err, [])

        requested_qnames = {n.name for n in requests if n.info}
        requirements, constraints = self._build_solver_inputs(
            requests,
            branch_pkgs,
            hard_pinned,
            soft_pinned,
            ignore_suggestions,
            requested_qnames,
        )

        error, solver_res = self.solve(
            requirements,
            constraints,
            requested_qnames,
            installed_qnames,
            {binfo.package.qualified_name() for binfo, _, _ in branch_pkgs},
            soft_pinned,
            ignore_suggestions,
        )
        if error:
            return (error, [])

        res: list[tuple[PackageInfo, str, bool]] = []
        for qn, raw_tag, is_sug in solver_res:
            res_node = self._graph.get(qn)
            if res_node is not None and res_node.info is not None:
                res.append((res_node.info, raw_tag, is_sug))

        for binfo, bversion, bsug in branch_pkgs:
            bqn = binfo.package.qualified_name()
            if bqn not in installed_qnames and bqn not in requested_qnames:
                res.append((binfo, bversion, bsug))

        return ("", res)

    def solve(
        self,
        requirements: Mapping[str, Range[semver.Version]],
        constraints: Mapping[str, Range[semver.Version]],
        requested_qnames: set[str],
        installed_qnames: set[str],
        branch_pkg_names: set[str],
        soft_pinned: dict[str, str],
        ignore_suggestions: bool,
    ) -> tuple[str, list[tuple[str, str, bool]]]:
        """Run the nab-resolver and return a topo-sorted install list.

        Returns a ``(error, items)`` pair.  On success ``error`` is empty and
        ``items`` is a list of ``(qname, raw_tag, is_suggestion)`` tuples in
        dependency order (dependencies before dependents).
        """
        resolver: Resolver[str, semver.Version] = Resolver(
            self,
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

        return (
            "",
            self._topo_sort(
                resolved,
                requested_qnames,
                installed_qnames,
                branch_pkg_names,
                soft_pinned,
                ignore_suggestions,
            ),
        )

    def _add_node(self, node: _Node) -> str:
        pkg_name = name_from_path(node.name)
        for existing_name in self._graph:
            if name_from_path(existing_name) == pkg_name and existing_name != node.name:
                return (
                    f'duplicate package name "{pkg_name}":'
                    f' remove one of "{existing_name}", "{node.name}"'
                )
        self._graph[node.name] = node
        return ""

    def _seed_installed(
        self,
        use_builtins: bool,
    ) -> tuple[dict[str, str], dict[str, str], set[str]]:
        """Populate graph with installed/builtin packages, return pin info.

        Returns ``(hard_pinned, soft_pinned, installed_qnames)``.
        """
        hard_pinned: dict[str, str] = {}
        soft_pinned: dict[str, str] = {}
        installed_qnames: set[str] = set()

        zeek_version = get_zeek_version()
        if zeek_version:
            zeek_node = _Node("zeek")
            zeek_node.installed_version = PackageVersion(
                TrackingMethod.VERSION,
                zeek_version,
            )
            self._graph["zeek"] = zeek_node
            hard_pinned["zeek"] = normalize_version_tag(zeek_version)

        zkg_node = _Node("zkg")
        zkg_node.installed_version = PackageVersion(
            TrackingMethod.VERSION,
            __version__,
        )
        self._graph["zkg"] = zkg_node
        hard_pinned["zkg"] = normalize_version_tag(__version__)

        if use_builtins:
            for binfo in self._manager.discover_builtin_packages():
                bname = binfo.package.qualified_name()
                if bname not in self._graph:
                    bnode = _Node(bname)
                    bnode.info = binfo
                    self._graph[bname] = bnode

        for ipkg in self._manager.installed_packages():
            iname = ipkg.package.qualified_name()
            installed_qnames.add(iname)
            if iname in self._graph:
                self._graph[iname].installed_version = PackageVersion(
                    ipkg.status.tracking_method,
                    ipkg.status.current_version,
                )
            else:
                iinfo = self._manager.info(iname, prefer_installed=True)
                inode = _Node(iname)
                inode.info = iinfo
                inode.installed_version = PackageVersion(
                    ipkg.status.tracking_method,
                    ipkg.status.current_version,
                )
                self._graph[iname] = inode
            installed_ver = ipkg.status.current_version
            if installed_ver:
                norm = normalize_version_tag(installed_ver)
                if ipkg.status.tracking_method is None:
                    hard_pinned[iname] = norm
                else:
                    soft_pinned[iname] = norm

        return hard_pinned, soft_pinned, installed_qnames

    def _walk_deps(
        self,
        ignore_suggestions: bool,
        use_builtins: bool,
    ) -> str:
        to_process = copy.copy(self._graph)
        while to_process:
            _, node = to_process.popitem()
            if node.info is None:
                continue
            best_tag = node.info.versions[-1] if node.info.versions else None
            if best_tag and node.info.metadata_file:
                clone_dir = os.path.dirname(node.info.metadata_file)
                node_clone = git.Repo(clone_dir)
                dd: dict[str, str] | None = _deps_at_version(node_clone, best_tag)
            else:
                dd = node.info.dependencies(field="depends") or {}
            ds = node.info.dependencies(field="suggests")

            if dd is None:
                return f'package "{node.name}" has malformed "depends" field'

            all_deps = dd.copy()

            if not ignore_suggestions:
                if ds is None:
                    return f'package "{node.name}" has malformed "suggests" field'
                all_deps.update(ds)

            for dep_name, _ in all_deps.items():
                if dep_name in ("zeek", "zkg"):
                    continue

                is_suggestion = node.is_suggestion or (
                    (ds is not None and dep_name in ds) and dep_name not in dd
                )

                info2 = None
                if use_builtins:
                    info2 = self._manager.find_builtin_package(dep_name)
                if info2 is None:
                    info2 = self._manager.info(dep_name, prefer_installed=False)

                if info2.invalid_reason:
                    return (
                        f'package "{node.name}" has invalid dependency'
                        f' "{dep_name}": {info2.invalid_reason}'
                    )

                dep_name_orig = dep_name
                dep_name = info2.package.qualified_name()
                LOG.debug(
                    'dependency "%s" of "%s" resolved to "%s"',
                    dep_name_orig,
                    node.name,
                    dep_name,
                )

                if dep_name in self._graph:
                    if self._graph[dep_name].is_suggestion and not is_suggestion:
                        self._graph[dep_name].is_suggestion = False
                    continue

                if dep_name in to_process:
                    if to_process[dep_name].is_suggestion and not is_suggestion:
                        to_process[dep_name].is_suggestion = False
                    continue

                node = _Node(dep_name)
                node.info = info2
                node.is_suggestion = is_suggestion
                if err := self._add_node(node):
                    return err
                to_process[node.name] = node

        return ""

    def _collect_branches(
        self,
        ignore_suggestions: bool,
    ) -> tuple[list[tuple[PackageInfo, str, bool]], str]:
        branch_pkgs: list[tuple[PackageInfo, str, bool]] = []
        branch_pkg_names: set[str] = set()

        for src_node in list(self._graph.values()):
            if src_node.info is None:
                continue
            src_deps: dict[str, str] = src_node.info.dependencies(field="depends") or {}
            if not ignore_suggestions:
                src_deps = {
                    **src_deps,
                    **(src_node.info.dependencies(field="suggests") or {}),
                }
            for dep_name, spec in src_deps.items():
                if not spec.startswith("branch="):
                    continue
                branch_name = spec[len("branch=") :]
                dep_info = self._lookup_dep(dep_name)
                if dep_info is None or dep_info.invalid_reason:
                    reason = dep_info.invalid_reason if dep_info else "unknown package"
                    return (
                        [],
                        f'package "{src_node.name}" has invalid dependency'
                        f' "{dep_name}": {reason}',
                    )
                qn = dep_info.package.qualified_name()
                if self._graph.get(qn) and self._graph[qn].installed_version:
                    iv = self._graph[qn].installed_version
                    assert iv
                    msg, ok = iv.fullfills(spec)
                    if not ok:
                        return (
                            [],
                            f'unsatisfiable dependency: "{qn}" ({iv.version}) is'
                            f' installed, but "{src_node.name}" requires'
                            f" {spec} ({msg})",
                        )
                elif qn not in branch_pkg_names:
                    branch_pkgs.append(
                        (dep_info, branch_name, src_node.is_suggestion),
                    )
                    branch_pkg_names.add(qn)

        return (branch_pkgs, "")

    def _build_solver_inputs(
        self,
        requests: list[_Node],
        branch_pkgs: list[tuple[PackageInfo, str, bool]],
        hard_pinned: dict[str, str],
        soft_pinned: dict[str, str],
        ignore_suggestions: bool,
        requested_qnames: set[str],
    ) -> tuple[dict[str, Range[semver.Version]], dict[str, Range[semver.Version]]]:
        requirements: dict[str, Range[semver.Version]] = {}
        for req_node in requests:
            assert req_node.info
            qname = req_node.name
            rv = req_node.requested_version
            if rv and rv.version:
                norm = normalize_version_tag(rv.version)
                if _is_versioned_package(norm):
                    requirements[qname] = Range.singleton(
                        semver.Version.coerce(norm),
                    )
                    continue
            requirements[qname] = Range.full()
        for binfo, _, _ in branch_pkgs:
            bqn = binfo.package.qualified_name()
            if bqn not in requirements and bqn not in hard_pinned:
                requirements[bqn] = Range.singleton(semver.Version("0.0.0"))

        constraints: dict[str, Range[semver.Version]] = {}
        for qname, norm in hard_pinned.items():
            if qname in requested_qnames:
                continue
            if _is_versioned_package(norm):
                constraints[qname] = Range.singleton(semver.Version.coerce(norm))
        for qname, norm in soft_pinned.items():
            if qname in requested_qnames:
                continue
            if _is_versioned_package(norm):
                constraints[qname] = Range.at_least(semver.Version.coerce(norm))

        for binfo, _, _ in branch_pkgs:
            bqn = binfo.package.qualified_name()
            synth_v = semver.Version("0.0.0")
            self._versions[bqn] = [synth_v]
            raw_bdeps: dict[str, str] = binfo.dependencies(field="depends") or {}
            if not ignore_suggestions:
                raw_bdeps = {
                    **raw_bdeps,
                    **(binfo.dependencies(field="suggests") or {}),
                }
            self._cache[(bqn, synth_v)] = (
                binfo.version_tag(),
                self._qualify_deps(raw_bdeps),
            )

        return requirements, constraints

    def _discover_versions(self) -> None:
        for qname, node in self._graph.items():
            if qname in self._versions:
                continue
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

    def prioritize(
        self,
        package: str,
        version_range: RangeProtocol[semver.Version],
        conflict_counts: Mapping[str, int],
        culprit_counts: Mapping[str, int] | None = None,
    ) -> int:
        return -len(self._versions.get(package, []))

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
            raw_deps = node.info.dependencies(field="depends") or {}
            return (node.info.version_tag(), self._qualify_deps(raw_deps))
        clone_dir = os.path.dirname(node.info.metadata_file)
        try:
            clone = git.Repo(clone_dir)
        except git.InvalidGitRepositoryError:
            raw_deps = node.info.dependencies(field="depends") or {}
            return (node.info.version_tag(), self._qualify_deps(raw_deps))
        found_tag: str | None = None
        for rt, nv in _semver_versions(git_version_tags(clone)):
            if semver.Version.coerce(nv) == version:
                found_tag = rt
                break
        if found_tag is None:
            raw_deps = node.info.dependencies(field="depends") or {}
            raw_tag = node.info.version_tag()
        else:
            raw_tag = found_tag
            raw_deps = _deps_at_version(clone, raw_tag)
        return (raw_tag, self._qualify_deps(raw_deps))

    def _lookup_dep(self, dep_name: str) -> PackageInfo | None:
        di = self._manager.find_builtin_package(dep_name)
        if di is not None:
            return di
        return self._lookup_info(dep_name)

    def _lookup_info(self, pkg_path: str) -> PackageInfo:
        key = canonical_url(pkg_path)
        if key not in self._info_cache:
            self._info_cache[key] = self._manager.info(
                pkg_path,
                prefer_installed=False,
            )
        return self._info_cache[key]

    def _resolve_raw_deps(self, raw: dict[str, str]) -> list[str]:
        result: list[str] = []
        for dep_s in raw:
            if dep_s in ("zeek", "zkg"):
                continue
            di = self._lookup_dep(dep_s)
            if di is not None and not di.invalid_reason:
                result.append(di.package.qualified_name())
        return result

    def _topo_sort(
        self,
        resolved: dict[str, semver.Version],
        requested_qnames: set[str],
        installed_qnames: set[str],
        branch_pkg_names: set[str],
        soft_pinned: dict[str, str],
        ignore_suggestions: bool,
    ) -> list[tuple[str, str, bool]]:
        suggestion_names: set[str] = {
            name for name, node in self._graph.items() if node.is_suggestion
        }

        def pkg_deps(qn: str) -> list[str]:
            nd = self._graph.get(qn)
            rv = resolved.get(qn)
            cached = self._cache.get((qn, rv)) if rv is not None else None

            if cached:
                deps = list(cached[1])
            elif nd and nd.info:
                deps = self._resolve_raw_deps(
                    nd.info.dependencies(field="depends") or {},
                )
            else:
                return []

            if not ignore_suggestions and nd and nd.info:
                for dqn in self._resolve_raw_deps(
                    nd.info.dependencies(field="suggests") or {},
                ):
                    if dqn not in deps:
                        deps.append(dqn)

            return sorted(deps)

        visited: set[str] = set()
        post_order: list[tuple[str, str, bool]] = []

        def dfs_emit(start: str) -> None:
            stack: list[tuple[str, bool]] = [(start, False)]
            while stack:
                qn, post = stack.pop()
                if post:
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
                    node = self._graph.get(qn)
                    if node is None or node.info is None:
                        continue
                    rv = resolved.get(qn)
                    ce = self._cache.get((qn, rv)) if rv is not None else None
                    raw_tag = ce[0] if ce else node.info.version_tag()
                    post_order.append((qn, raw_tag, qn in suggestion_names))
                else:
                    if qn in visited:
                        continue
                    visited.add(qn)
                    stack.append((qn, True))
                    for dep_qn in reversed(pkg_deps(qn)):
                        if dep_qn not in visited:
                            stack.append((dep_qn, False))

        for seed in list(requested_qnames) + list(branch_pkg_names):
            dfs_emit(seed)

        seen: set[str] = set()
        res: list[tuple[str, str, bool]] = []
        for qn, raw_tag, is_sug in reversed(post_order):
            if qn not in seen:
                seen.add(qn)
                res.append((qn, raw_tag, is_sug))

        return res
