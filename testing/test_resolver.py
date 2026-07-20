"""Unit tests for zeekpkg._resolver internals."""

import pathlib
from unittest.mock import MagicMock, patch

import git
import pytest
import semantic_version as semver
from nab_resolver.ranges import Range

from zeekpkg._resolver import (
    _constraint_to_range,
    _fmt_range,
    _Node,
    _normalize_constraint,
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
    # Pre-populate versions so choose_version works.
    provider._versions[qname] = [v]
    deps1 = provider.get_dependencies(qname, v)
    deps2 = provider.get_dependencies(qname, v)
    assert deps1 is deps2


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
