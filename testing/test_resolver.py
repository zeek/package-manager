"""Unit tests for zeekpkg._resolver internals."""

import pathlib
from typing import cast
from unittest.mock import MagicMock, patch

import git
import pytest
import semantic_version as semver
from nab_resolver.ranges import Range

from zeekpkg._resolver import (
    _constraint_to_range,
    _deps_at_version,
    _fmt_range,
    _FmtRange,
    _get_branch_names,
    _is_versioned_package,
    _Node,
    _normalize_constraint,
    _run_solver,
    _ZkgProvider,
)
from zeekpkg.manager import Manager
from zeekpkg.package import PackageInfo


@pytest.fixture()
def manager(tmp_path: pathlib.Path) -> Manager:
    return Manager(
        state_dir=str(tmp_path / "state"),
        script_dir=str(tmp_path / "scripts"),
        plugin_dir=str(tmp_path / "plugins"),
    )


def _make_tagged_repo(
    tmp_path: pathlib.Path,
    name: str,
    tags_deps: list[tuple[str, str]],
) -> git.Repo:
    """Create a git repo with tagged commits each carrying a zkg.meta.

    *tags_deps* is a list of (tag, depends_line) tuples where depends_line
    is the raw value for the 'depends' field (e.g., "dep-a >=1.0.0").
    """
    path = tmp_path / name
    path.mkdir()
    r = git.Repo.init(path, initial_branch="main")
    r.config_writer().set_value("user", "name", "Test").release()
    r.config_writer().set_value("user", "email", "test@test").release()
    for tag, depends in tags_deps:
        meta = f"[package]\ndescription = {name}\n"
        if depends:
            meta += f"depends = {depends}\n"
        (path / "zkg.meta").write_text(meta)
        r.index.add(["zkg.meta"])
        r.index.commit(f"release {tag}")
        r.create_tag(tag)
    return r


def _provider_with_repo(
    manager: Manager,
    tmp_path: pathlib.Path,
    qname: str,
    tags_deps: list[tuple[str, str]],
) -> tuple[_ZkgProvider, git.Repo]:
    """Build a minimal _ZkgProvider with one git-backed package."""
    repo = _make_tagged_repo(tmp_path, qname.rsplit("/", maxsplit=1)[-1], tags_deps)
    info = MagicMock(spec=PackageInfo)
    info.metadata_file = str(pathlib.Path(str(repo.working_dir)) / "zkg.meta")
    info.metadata_version = None
    info.invalid_reason = None

    node = _Node(qname)
    node.info = info
    graph = {qname: node}
    return _ZkgProvider(manager, graph), repo


def test_node_str() -> None:
    n = _Node("org/pkg")
    s = str(n)
    assert "org/pkg" in s
    assert "requested" in s
    assert "installed" in s


def test_get_branch_names(tmp_path: pathlib.Path) -> None:
    repo = git.Repo.init(tmp_path / "br-repo", initial_branch="main")
    repo.config_writer().set_value("user", "name", "Test").release()
    repo.config_writer().set_value("user", "email", "test@test").release()
    (tmp_path / "br-repo" / "f").write_text("x")
    repo.index.add(["f"])
    repo.index.commit("init")
    # Simulate remote tracking refs by creating refs/remotes/origin/main manually.
    repo.git.update_ref("refs/remotes/origin/main", "HEAD")
    branches = _get_branch_names(repo)
    assert "main" in branches


def test_normalize_bare_equals() -> None:
    assert _normalize_constraint("=1.0.0") == "==1.0.0"


def test_normalize_double_equals_unchanged() -> None:
    assert _normalize_constraint("==1.0.0") == "==1.0.0"


def test_normalize_gte_unchanged() -> None:
    assert _normalize_constraint(">=1.0.0") == ">=1.0.0"


def test_normalize_wildcard_unchanged() -> None:
    assert _normalize_constraint("*") == "*"


def test_constraint_to_range_gte() -> None:
    r = _constraint_to_range(">=1.0.0")
    assert semver.Version("1.0.0") in r
    assert semver.Version("2.0.0") in r
    assert semver.Version("0.9.0") not in r


def test_constraint_to_range_exact() -> None:
    r = _constraint_to_range("==1.0.0")
    assert semver.Version("1.0.0") in r
    assert semver.Version("1.0.1") not in r


def test_constraint_to_range_bare_equals() -> None:
    r = _constraint_to_range("=1.0.0")
    assert semver.Version("1.0.0") in r
    assert semver.Version("1.0.1") not in r


def test_constraint_to_range_wildcard() -> None:
    r = _constraint_to_range("*")
    assert semver.Version("1.0.0") in r
    assert semver.Version("99.0.0") in r


def test_constraint_to_range_gt() -> None:
    r = _constraint_to_range(">1.0.0")
    assert semver.Version("1.0.1") in r
    assert semver.Version("1.0.0") not in r


def test_constraint_to_range_lte() -> None:
    r = _constraint_to_range("<=1.0.0")
    assert semver.Version("1.0.0") in r
    assert semver.Version("1.0.1") not in r


def test_constraint_to_range_compound() -> None:
    r = _constraint_to_range(">=1.0.0,<2.0.0")
    assert semver.Version("1.5.0") in r
    assert semver.Version("2.0.0") not in r
    assert semver.Version("0.9.0") not in r


def test_choose_version_picks_highest_in_range(
    manager: Manager,
    tmp_path: pathlib.Path,
) -> None:
    qname = "org/pkg"
    provider, _ = _provider_with_repo(
        manager,
        tmp_path,
        qname,
        [("v1.0.0", ""), ("v1.5.0", ""), ("v2.0.0", "")],
    )
    r = _constraint_to_range(">=1.0.0,<2.0.0")
    chosen = provider.choose_version(qname, r)
    assert chosen == semver.Version("1.5.0")


def test_choose_version_returns_none_when_no_match(
    manager: Manager,
    tmp_path: pathlib.Path,
) -> None:
    qname = "org/pkg"
    provider, _ = _provider_with_repo(
        manager,
        tmp_path,
        qname,
        [("v1.0.0", "")],
    )
    r = _constraint_to_range(">=2.0.0")
    assert provider.choose_version(qname, r) is None


def test_choose_version_unknown_package_returns_none(
    manager: Manager,
    tmp_path: pathlib.Path,
) -> None:
    provider, _ = _provider_with_repo(
        manager,
        tmp_path,
        "org/pkg",
        [("v1.0.0", "")],
    )
    assert provider.choose_version("org/other", Range.full()) is None


def test_has_satisfying_version_true(
    manager: Manager,
    tmp_path: pathlib.Path,
) -> None:
    qname = "org/pkg"
    provider, _ = _provider_with_repo(
        manager,
        tmp_path,
        qname,
        [("v1.0.0", ""), ("v2.0.0", "")],
    )
    assert provider.has_satisfying_version(qname, _constraint_to_range(">=1.0.0"))


def test_has_satisfying_version_false(
    manager: Manager,
    tmp_path: pathlib.Path,
) -> None:
    qname = "org/pkg"
    provider, _ = _provider_with_repo(
        manager,
        tmp_path,
        qname,
        [("v1.0.0", "")],
    )
    assert not provider.has_satisfying_version(qname, _constraint_to_range(">=2.0.0"))


def test_get_dependencies_caches_result(
    manager: Manager,
    tmp_path: pathlib.Path,
) -> None:
    qname = "org/pkg"
    dep_repo = _make_tagged_repo(
        tmp_path,
        "dep",
        [("v1.0.0", "")],
    )
    provider, _ = _provider_with_repo(
        manager,
        tmp_path,
        qname,
        [("v1.0.0", f"{dep_repo.working_dir} >=1.0.0")],
    )
    v = semver.Version("1.0.0")
    provider._versions[qname] = [v]
    with patch.object(provider, "_fetch_deps", wraps=provider._fetch_deps) as spy:
        provider.get_dependencies(qname, v)
        provider.get_dependencies(qname, v)
        assert spy.call_count == 1


def test_get_dependencies_empty_for_unknown(
    manager: Manager,
    tmp_path: pathlib.Path,
) -> None:
    provider, _ = _provider_with_repo(
        manager,
        tmp_path,
        "org/pkg",
        [("v1.0.0", "")],
    )
    deps = provider.get_dependencies("org/unknown", semver.Version("1.0.0"))
    assert deps == {}


def test_provider_init_git_error_falls_back_gracefully(
    manager: Manager,
    tmp_path: pathlib.Path,
) -> None:
    # metadata_file present but git.Repo() raises -- provider must not crash.
    info = MagicMock(spec=PackageInfo)
    info.metadata_file = str(tmp_path / "not-a-repo" / "zkg.meta")
    info.metadata_version = "1.0.0"
    info.invalid_reason = None
    info.versions = []
    node = _Node("org/broken")
    node.info = info
    provider = _ZkgProvider(manager, {"org/broken": node})
    # Falls back to metadata_version.
    assert semver.Version("1.0.0") in provider._versions.get("org/broken", [])


def test_provider_init_no_metadata_file(
    manager: Manager,
    tmp_path: pathlib.Path,
) -> None:
    # Node with no metadata_file (builtin/directory package) -- provider
    # falls through to the version-from-metadata path immediately.
    info = MagicMock(spec=PackageInfo)
    info.metadata_file = None
    info.metadata_version = "2.0.0"
    info.invalid_reason = None
    info.versions = []
    node = _Node("org/builtin")
    node.info = info
    provider = _ZkgProvider(manager, {"org/builtin": node})
    assert semver.Version("2.0.0") in provider._versions.get("org/builtin", [])


def test_fetch_deps_no_metadata_file(
    manager: Manager,
    tmp_path: pathlib.Path,
) -> None:
    # _fetch_deps takes the no-metadata_file branch and reads current metadata.
    info = MagicMock(spec=PackageInfo)
    info.metadata_file = None
    info.metadata_version = "1.0.0"
    info.best_version.return_value = "1.0.0"
    info.dependencies.return_value = {}
    info.invalid_reason = None
    info.versions = []
    node = _Node("org/builtin")
    node.info = info
    provider = _ZkgProvider(manager, {"org/builtin": node})
    provider._versions["org/builtin"] = [semver.Version("1.0.0")]
    deps = provider.get_dependencies("org/builtin", semver.Version("1.0.0"))
    assert deps == {}


def test_provider_init_version_from_versions_list(manager: Manager) -> None:
    # No metadata_version; fall back to the last entry in info.versions.
    info = MagicMock(spec=PackageInfo)
    info.metadata_file = None
    info.metadata_version = None
    info.installed_version = None
    info.versions = ["1.1.0", "1.2.0"]
    info.invalid_reason = None
    node = _Node("org/pkg")
    node.info = info
    provider = _ZkgProvider(manager, {"org/pkg": node})
    assert semver.Version("1.2.0") in provider._versions.get("org/pkg", [])


def test_provider_init_version_coercion_failure_falls_back_to_zero(
    manager: Manager,
) -> None:
    # versions[-1] is not a valid semver string -- coerce raises ValueError and
    # we fall through to the 0.0.0 sentinel.
    info = MagicMock(spec=PackageInfo)
    info.metadata_file = None
    info.metadata_version = None
    info.installed_version = None
    info.versions = ["not-a-version"]
    info.invalid_reason = None
    node = _Node("org/pkg")
    node.info = info
    provider = _ZkgProvider(manager, {"org/pkg": node})
    assert provider._versions.get("org/pkg") == [semver.Version("0.0.0")]


def test_provider_init_falls_back_to_zero_version(manager: Manager) -> None:
    # No version inferable from any source -- provider must register 0.0.0 so
    # the solver can still attempt resolution.
    info = MagicMock(spec=PackageInfo)
    info.metadata_file = None
    info.metadata_version = None
    info.installed_version = None
    info.versions = []
    info.invalid_reason = None
    node = _Node("org/pkg")
    node.info = info
    provider = _ZkgProvider(manager, {"org/pkg": node})
    assert provider._versions.get("org/pkg") == [semver.Version("0.0.0")]


def test_fetch_deps_non_git_directory(manager: Manager, tmp_path: pathlib.Path) -> None:
    # metadata_file points inside a plain directory (not a git repo) --
    # _fetch_deps must fall through to the InvalidGitRepositoryError handler.
    plain_dir = tmp_path / "plain"
    plain_dir.mkdir()
    meta_file = plain_dir / "zkg.meta"
    meta_file.write_text("[package]\ndescription = plain\n")
    info = MagicMock(spec=PackageInfo)
    info.metadata_file = str(meta_file)
    info.metadata_version = "1.0.0"
    info.best_version.return_value = "1.0.0"
    info.dependencies.return_value = {}
    info.invalid_reason = None
    info.versions = []
    node = _Node("org/plain")
    node.info = info
    provider = _ZkgProvider(manager, {"org/plain": node})
    provider._versions["org/plain"] = [semver.Version("1.0.0")]
    deps = provider.get_dependencies("org/plain", semver.Version("1.0.0"))
    assert deps == {}


def test_fetch_deps_synthetic_version(
    manager: Manager,
    tmp_path: pathlib.Path,
) -> None:
    # Version 9.9.9 has no matching tag -- _fetch_deps takes the synthetic
    # version branch and reads current HEAD metadata instead.
    repo = _make_tagged_repo(tmp_path, "synth", [("v1.0.0", "")])
    info = MagicMock(spec=PackageInfo)
    info.metadata_file = str(pathlib.Path(str(repo.working_dir)) / "zkg.meta")
    info.metadata_version = "9.9.9"
    info.best_version.return_value = "9.9.9"
    info.dependencies.return_value = {}
    info.invalid_reason = None
    info.versions = []
    node = _Node("org/synth")
    node.info = info
    provider = _ZkgProvider(manager, {"org/synth": node})
    provider._versions["org/synth"] = [semver.Version("9.9.9")]
    deps = provider.get_dependencies("org/synth", semver.Version("9.9.9"))
    assert deps == {}


def test_narrow_for_display_wraps_in_fmt_range(
    manager: Manager,
    tmp_path: pathlib.Path,
) -> None:
    provider, _ = _provider_with_repo(manager, tmp_path, "org/pkg", [("v1.0.0", "")])
    raw = Range.at_least(semver.Version("1.0.0"))
    result = provider.narrow_for_display("org/pkg", raw)
    assert isinstance(result, _FmtRange)
    assert "inf" not in str(result)


# ---------------------------------------------------------------------------
# _fmt_range -- tests focus on cases where nab-resolver's raw interval
# notation would expose sentinel strings like "(-inf, X) | (X, +inf)"
# ---------------------------------------------------------------------------


def test_fmt_range_open_upper_bound() -> None:
    # Range.at_least internally stores (-inf sentinel, X, +inf sentinel).
    # _fmt_range must produce ">=X", not expose sentinel strings.
    r = Range.at_least(semver.Version("1.0.0"))
    result = _fmt_range(r)
    assert "inf" not in result
    assert result == ">=1.0.0"


def test_fmt_range_excluded_version_no_inf() -> None:
    # Excluding a single version produces two half-open intervals with -inf/+inf
    # sentinels. _fmt_range must render them as operator-prefixed strings.
    r = Range.less_than(semver.Version("1.0.0")) | Range.greater_than(
        semver.Version("1.0.0"),
    )
    result = _fmt_range(r)
    assert "inf" not in result
    assert result == "<1.0.0 | >1.0.0"


def test_fmt_range_exact_point() -> None:
    r = Range.singleton(semver.Version("1.2.3"))
    assert _fmt_range(r) == "=1.2.3"


def test_fmt_range_full_is_wildcard() -> None:
    assert _fmt_range(Range.full()) == "*"


def test_fmt_range_subclass_str() -> None:
    r = _FmtRange(Range.at_least(semver.Version("1.0.0"))._intervals)
    assert str(r) == ">=1.0.0"
    assert "inf" not in str(r)


def test_fmt_range_and_preserves_subclass() -> None:
    a = _FmtRange(Range.at_least(semver.Version("1.0.0"))._intervals)
    b = _FmtRange(Range.less_than(semver.Version("2.0.0"))._intervals)
    result = a & b
    assert isinstance(result, _FmtRange)
    assert str(result) == ">=1.0.0, <2.0.0"


def test_fmt_range_or_preserves_subclass() -> None:
    a = _FmtRange(Range.less_than(semver.Version("1.0.0"))._intervals)
    b = _FmtRange(Range.greater_than(semver.Version("2.0.0"))._intervals)
    result = a | b
    assert isinstance(result, _FmtRange)
    assert "inf" not in str(result)


def test_fmt_range_invert_preserves_subclass() -> None:
    r = _FmtRange(Range.singleton(semver.Version("1.0.0"))._intervals)
    result = ~r
    assert isinstance(result, _FmtRange)
    assert "inf" not in str(result)


def test_fmt_range_sub_preserves_subclass() -> None:
    a = _FmtRange(Range.at_least(semver.Version("1.0.0"))._intervals)
    b = _FmtRange(Range.singleton(semver.Version("1.5.0"))._intervals)
    result = a - b
    assert isinstance(result, _FmtRange)
    assert "inf" not in str(result)


def test_fmt_range_empty_classmethod() -> None:
    r = _FmtRange.empty()
    assert isinstance(r, _FmtRange)
    assert semver.Version("1.0.0") not in r


def test_fmt_range_full_classmethod() -> None:
    r = _FmtRange.full()
    assert isinstance(r, _FmtRange)
    assert semver.Version("1.0.0") in r


def test_fmt_range_singleton_classmethod() -> None:
    r = _FmtRange.singleton(semver.Version("2.0.0"))
    assert isinstance(r, _FmtRange)
    assert semver.Version("2.0.0") in r
    assert semver.Version("1.0.0") not in r


def test_constraint_to_range_returns_fmt_range() -> None:
    assert isinstance(_constraint_to_range(">=1.0.0"), _FmtRange)
    assert isinstance(_constraint_to_range("*"), _FmtRange)
    assert isinstance(_constraint_to_range("=1.0.0"), _FmtRange)


def _make_pkg_repo_with_deps(
    tmp_path: pathlib.Path,
    name: str,
    versions: list[tuple[str, str]],
) -> git.Repo:
    """Create a git repo with tagged versions, each having a zkg.meta.

    *versions* is a list of (tag, depends_line) tuples where depends_line
    is the raw value for the 'depends' field (e.g. "dep-a >=1.0.0").
    """
    r = git.Repo.init(tmp_path / name, initial_branch="main")
    r.config_writer().set_value("user", "name", "Test").release()
    r.config_writer().set_value("user", "email", "test@test").release()
    for tag, depends in versions:
        meta = f"[package]\ndescription = {name}\n"
        if depends:
            meta += f"depends = {depends}\n"
        (tmp_path / name / "zkg.meta").write_text(meta)
        r.index.add(["zkg.meta"])
        r.index.commit(f"version {tag}")
        r.create_tag(tag)
    return r


def test_zkgprovider_qualified_names_via_resolve(
    manager: Manager,
    tmp_path: pathlib.Path,
) -> None:
    dep_repo = _make_pkg_repo_with_deps(tmp_path, "dep-pkg", [("v1.0.0", "")])
    pkg_repo = _make_pkg_repo_with_deps(
        tmp_path,
        "main-pkg",
        [("v1.0.0", f"{dep_repo.working_dir} >=1.0.0")],
    )
    err, _ = manager.validate_dependencies([(str(pkg_repo.working_dir), "")])
    assert err == ""


def test_zkgprovider_strips_branch_deps(
    manager: Manager,
    tmp_path: pathlib.Path,
) -> None:
    pkg_repo = _make_pkg_repo_with_deps(
        tmp_path,
        "pkg-with-branch-dep",
        [("v1.0.0", "some-dep branch=main")],
    )
    manager.validate_dependencies([(str(pkg_repo.working_dir), "")])


def test_zkgprovider_strips_zeek_zkg(
    manager: Manager,
    tmp_path: pathlib.Path,
) -> None:
    pkg_repo = _make_pkg_repo_with_deps(
        tmp_path,
        "pkg-with-zeek-dep",
        [("v1.0.0", "zeek >=5.0.0 zkg >=3.0.0")],
    )
    manager.validate_dependencies([(str(pkg_repo.working_dir), "")])


def test_qualify_deps_skips_invalid_dep(manager: Manager) -> None:
    # _qualify_deps must silently drop a dep whose PackageInfo has invalid_reason
    # set rather than forwarding it to the solver.
    invalid_info = MagicMock(spec=PackageInfo)
    invalid_info.invalid_reason = "not a valid package"
    provider = _ZkgProvider(manager, {})
    with (
        patch.object(manager, "find_builtin_package", return_value=None),
        patch.object(manager, "info", return_value=invalid_info),
    ):
        result = provider._qualify_deps({"bad-dep": ">=1.0.0"})
    assert result == {}


def test_get_dependencies_skips_unparseable_spec(
    manager: Manager,
    tmp_path: pathlib.Path,
) -> None:
    # An unparseable constraint in deps_str must be silently skipped rather than
    # propagated to the solver.
    provider, _ = _provider_with_repo(manager, tmp_path, "org/pkg", [("v1.0.0", "")])
    v = semver.Version("1.0.0")
    provider._cache[("org/pkg", v)] = ("v1.0.0", {"org/dep": "totally-invalid!"})
    deps = provider.get_dependencies("org/pkg", v)
    assert "org/dep" not in deps


def test_deps_at_version_no_metadata_file(tmp_path: pathlib.Path) -> None:
    # Tag exists but has no zkg.meta or bro-pkg.meta -- returns empty dict.
    repo = git.Repo.init(tmp_path / "nometarepo", initial_branch="main")
    repo.config_writer().set_value("user", "name", "Test").release()
    repo.config_writer().set_value("user", "email", "test@test").release()
    (tmp_path / "nometarepo" / "README").write_text("no meta here")
    repo.index.add(["README"])
    repo.index.commit("initial")
    repo.create_tag("v1.0.0")
    assert _deps_at_version(repo, "v1.0.0") == {}


def test_deps_at_version_missing_package_section(tmp_path: pathlib.Path) -> None:
    # zkg.meta at the tag has no [package] section -- returns empty dict.
    repo = git.Repo.init(tmp_path / "badsectrepo", initial_branch="main")
    repo.config_writer().set_value("user", "name", "Test").release()
    repo.config_writer().set_value("user", "email", "test@test").release()
    (tmp_path / "badsectrepo" / "zkg.meta").write_text("[other]\nkey = val\n")
    repo.index.add(["zkg.meta"])
    repo.index.commit("initial")
    repo.create_tag("v1.0.0")
    assert _deps_at_version(repo, "v1.0.0") == {}


def test_is_versioned_package_sha() -> None:
    assert not _is_versioned_package("a" * 40)


def test_is_versioned_package_branch_name() -> None:
    assert not _is_versioned_package("main")


def test_is_versioned_package_valid_semver() -> None:
    assert _is_versioned_package("1.2.3")


def _make_conflicting_provider(
    manager: Manager,
    tmp_path: pathlib.Path,
) -> tuple[_ZkgProvider, dict[str, _Node]]:
    """Build two packages whose constraints conflict."""
    repo_a = _make_tagged_repo(tmp_path, "pkg-a", [("v1.0.0", ""), ("v2.0.0", "")])
    info_a = MagicMock(spec=PackageInfo)
    info_a.metadata_file = str(pathlib.Path(str(repo_a.working_dir)) / "zkg.meta")
    info_a.metadata_version = None
    info_a.invalid_reason = None
    node_a = _Node("org/pkg-a")
    node_a.info = info_a

    graph = {"org/pkg-a": node_a}
    provider = _ZkgProvider(manager, graph)
    return provider, graph


def test_run_solver_resolution_error(
    manager: Manager,
    tmp_path: pathlib.Path,
) -> None:
    provider, graph = _make_conflicting_provider(manager, tmp_path)
    # Require >=2.0.0 and <1.0.0 simultaneously -- unsatisfiable.
    requirements = {
        "org/pkg-a": _constraint_to_range(">=2.0.0"),
    }
    constraints = {
        "org/pkg-a": _constraint_to_range("<1.0.0"),
    }
    err, items = _run_solver(
        provider,
        requirements,
        constraints,
        graph,
        requested_qnames={"org/pkg-a"},
        installed_qnames=set(),
        branch_pkg_names=set(),
        soft_pinned={},
        ignore_suggestions=True,
        lookup_dep=lambda _: None,
    )
    assert err != ""
    assert items == []


def _make_two_package_setup(
    manager: Manager,
    tmp_path: pathlib.Path,
    *,
    dep_suggests: bool = False,
    dep_info_none: bool = False,
) -> tuple[_ZkgProvider, dict[str, _Node]]:
    """Build provider+graph where main-pkg depends on dep-pkg (or suggests it).

    The solver-cache for main-pkg is pre-populated so that get_dependencies
    returns dep-pkg as a dependency without needing real git metadata fetching.
    """
    dep_repo = _make_tagged_repo(tmp_path, "dep-pkg", [("v1.0.0", "")])
    dep_info = MagicMock(spec=PackageInfo)
    dep_info.metadata_file = str(
        pathlib.Path(str(dep_repo.working_dir)) / "zkg.meta",
    )
    dep_info.metadata_version = None
    dep_info.invalid_reason = None
    dep_info.dependencies.return_value = {}
    dep_info.best_version.return_value = "v1.0.0"
    dep_node = _Node("org/dep-pkg")
    if not dep_info_none:
        dep_node.info = dep_info

    main_repo = _make_tagged_repo(tmp_path, "main-pkg", [("v1.0.0", "")])
    main_info = MagicMock(spec=PackageInfo)
    main_info.metadata_file = str(
        pathlib.Path(str(main_repo.working_dir)) / "zkg.meta",
    )
    main_info.metadata_version = None
    main_info.invalid_reason = None
    main_info.best_version.return_value = "v1.0.0"
    if dep_suggests:
        main_info.dependencies.side_effect = lambda field="depends": (
            {"dep-pkg": ">=1.0.0"} if field == "suggests" else {}
        )
    else:
        main_info.dependencies.return_value = {}
    main_node = _Node("org/main-pkg")
    main_node.info = main_info

    graph: dict[str, _Node] = {"org/main-pkg": main_node, "org/dep-pkg": dep_node}
    provider = _ZkgProvider(manager, graph)

    # Pre-populate cache: main-pkg v1.0.0 depends on org/dep-pkg >=1.0.0.
    v_main = semver.Version("1.0.0")
    v_dep = semver.Version("1.0.0")
    if not dep_suggests:
        provider._cache[("org/main-pkg", v_main)] = (
            "v1.0.0",
            {"org/dep-pkg": ">=1.0.0"},
        )
    else:
        provider._cache[("org/main-pkg", v_main)] = ("v1.0.0", {})
    provider._cache[("org/dep-pkg", v_dep)] = ("v1.0.0", {})
    if "org/dep-pkg" not in provider._versions:
        provider._versions["org/dep-pkg"] = [v_dep]

    return provider, graph


def test_run_solver_dep_emitted_in_result(
    manager: Manager,
    tmp_path: pathlib.Path,
) -> None:
    # Transitive dep (not requested) resolved and emitted via _dfs_emit.
    provider, graph = _make_two_package_setup(manager, tmp_path)
    requirements = {"org/main-pkg": _constraint_to_range(">=1.0.0")}
    err, items = _run_solver(
        provider,
        requirements,
        {},
        graph,
        requested_qnames={"org/main-pkg"},
        installed_qnames=set(),
        branch_pkg_names=set(),
        soft_pinned={},
        ignore_suggestions=True,
        lookup_dep=lambda _: None,
    )
    assert err == ""
    qnames = [qn for qn, _, _ in items]
    assert "org/dep-pkg" in qnames


def test_run_solver_with_suggestions(
    manager: Manager,
    tmp_path: pathlib.Path,
) -> None:
    # ignore_suggestions=False exercises the _pkg_deps suggestions path (lines 409-415).
    dep_info = MagicMock(spec=PackageInfo)
    dep_info.invalid_reason = None
    dep_info.package = MagicMock()
    dep_info.package.qualified_name.return_value = "org/dep-pkg"
    provider, graph = _make_two_package_setup(
        manager,
        tmp_path,
        dep_suggests=True,
    )
    requirements = {"org/main-pkg": _constraint_to_range(">=1.0.0")}
    err, items = _run_solver(
        provider,
        requirements,
        {},
        graph,
        requested_qnames={"org/main-pkg"},
        installed_qnames=set(),
        branch_pkg_names=set(),
        soft_pinned={},
        ignore_suggestions=False,
        lookup_dep=lambda name: dep_info if name == "dep-pkg" else None,
    )
    assert err == ""
    assert "org/dep-pkg" in [qn for qn, _, _ in items]


def test_run_solver_suggestions_skips_zeek_zkg(
    manager: Manager,
    tmp_path: pathlib.Path,
) -> None:
    # "zeek"/"zkg" in suggests must be silently skipped (line 410).
    dep_info = MagicMock(spec=PackageInfo)
    dep_info.invalid_reason = None
    dep_info.package = MagicMock()
    dep_info.package.qualified_name.return_value = "org/dep-pkg"
    provider, graph = _make_two_package_setup(
        manager,
        tmp_path,
        dep_suggests=True,
    )
    cast(MagicMock, graph["org/main-pkg"].info).dependencies.side_effect = (
        lambda field="depends": (
            {"zeek": ">=5.0.0", "dep-pkg": ">=1.0.0"} if field == "suggests" else {}
        )
    )
    requirements = {"org/main-pkg": _constraint_to_range(">=1.0.0")}
    err, _ = _run_solver(
        provider,
        requirements,
        {},
        graph,
        requested_qnames={"org/main-pkg"},
        installed_qnames=set(),
        branch_pkg_names=set(),
        soft_pinned={},
        ignore_suggestions=False,
        lookup_dep=lambda name: dep_info if name == "dep-pkg" else None,
    )
    assert err == ""


def test_run_solver_branch_pkg_with_suggests(
    manager: Manager,
    tmp_path: pathlib.Path,
) -> None:
    # Package in branch_pkg_names is not in resolved -- exercises lines 417-428
    # (_pkg_deps fallback reading deps/suggests from graph directly).
    dep_info = MagicMock(spec=PackageInfo)
    dep_info.invalid_reason = None
    dep_info.package = MagicMock()
    dep_info.package.qualified_name.return_value = "org/dep-pkg"
    provider, graph = _make_two_package_setup(
        manager,
        tmp_path,
        dep_suggests=True,
    )
    cast(MagicMock, graph["org/main-pkg"].info).dependencies.side_effect = (
        lambda field="depends": (
            {"zeek": ">=5.0.0", "dep-pkg": ">=1.0.0"}
            if field in ("depends", "suggests")
            else {}
        )
    )
    requirements: dict[str, Range[semver.Version]] = {}
    err, _ = _run_solver(
        provider,
        requirements,
        {},
        graph,
        requested_qnames=set(),
        installed_qnames=set(),
        branch_pkg_names={"org/main-pkg"},
        soft_pinned={},
        ignore_suggestions=False,
        lookup_dep=lambda name: dep_info if name == "dep-pkg" else None,
    )
    assert err == ""


def test_run_solver_dfs_skips_revisit(
    manager: Manager,
    tmp_path: pathlib.Path,
) -> None:
    # A seed that also appears as a branch_pkg causes it to be revisited by
    # _dfs_emit, hitting the dfs_visited guard (line 467) on the second pass.
    repo = _make_tagged_repo(tmp_path, "solo", [("v1.0.0", "")])
    info = MagicMock(spec=PackageInfo)
    info.metadata_file = str(pathlib.Path(str(repo.working_dir)) / "zkg.meta")
    info.metadata_version = None
    info.invalid_reason = None
    info.dependencies.return_value = {}
    info.best_version.return_value = "v1.0.0"
    node = _Node("org/solo")
    node.info = info
    graph = {"org/solo": node}
    provider = _ZkgProvider(manager, graph)
    v = semver.Version("1.0.0")
    provider._cache[("org/solo", v)] = ("v1.0.0", {})
    requirements = {"org/solo": _constraint_to_range(">=1.0.0")}
    # Appear in both requested_qnames and branch_pkg_names -- two seeds that
    # collapse to the same package, so the second _dfs_emit hits dfs_visited.
    err, _ = _run_solver(
        provider,
        requirements,
        {},
        graph,
        requested_qnames={"org/solo"},
        installed_qnames=set(),
        branch_pkg_names={"org/solo"},
        soft_pinned={},
        ignore_suggestions=True,
        lookup_dep=lambda _: None,
    )
    assert err == ""


def test_run_solver_dfs_node_no_info(
    manager: Manager,
    tmp_path: pathlib.Path,
) -> None:
    # Dep node with info=None is skipped by _dfs_emit (covers line 454).
    provider, graph = _make_two_package_setup(
        manager,
        tmp_path,
        dep_info_none=True,
    )
    requirements = {"org/main-pkg": _constraint_to_range(">=1.0.0")}
    err, items = _run_solver(
        provider,
        requirements,
        {},
        graph,
        requested_qnames={"org/main-pkg"},
        installed_qnames=set(),
        branch_pkg_names=set(),
        soft_pinned={},
        ignore_suggestions=True,
        lookup_dep=lambda _: None,
    )
    assert err == ""
    qnames = [qn for qn, _, _ in items]
    assert "org/dep-pkg" not in qnames
