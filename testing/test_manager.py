"""Unit tests for zeekpkg.manager internals."""

import pathlib
from unittest.mock import patch

import git
import pytest

from zeekpkg.manager import (
    GitResolution,
    Manager,
    _info_from_clone,
    _is_git_package,
    _resolve_git_version,
    _snapshot_from_git_repo,
)
from zeekpkg.package import (
    TRACKING_METHOD_BRANCH,
    TRACKING_METHOD_BUILTIN,
    TRACKING_METHOD_COMMIT,
    TRACKING_METHOD_VERSION,
    InstalledPackage,
    Package,
    PackageInfo,
    PackageSnapshot,
    PackageStatus,
)


@pytest.fixture()
def manager(tmp_path: pathlib.Path) -> Manager:
    return Manager(
        state_dir=str(tmp_path / "state"),
        script_dir=str(tmp_path / "scripts"),
        plugin_dir=str(tmp_path / "plugins"),
    )


@pytest.fixture()
def repo(tmp_path: pathlib.Path) -> git.Repo:
    """A minimal git repo with a single commit on 'main' and a self-remote.

    The self-pointing origin remote ensures _is_branch_outdated can resolve
    origin/main without network access.
    """
    r = git.Repo.init(tmp_path / "origin", initial_branch="main")
    r.config_writer().set_value("user", "name", "Test").release()
    r.config_writer().set_value("user", "email", "test@test").release()
    (tmp_path / "origin" / "file.txt").write_text("hello")
    r.index.add(["file.txt"])
    r.index.commit("initial commit")
    return r.clone(str(tmp_path / "clone"))


def test_defaults_to_branch_when_no_tags(repo: git.Repo) -> None:
    resolution = _resolve_git_version(repo, "")
    assert isinstance(resolution, GitResolution)
    assert resolution.tracking_method == TRACKING_METHOD_BRANCH
    assert resolution.version == "main"


def test_defaults_to_latest_tag(repo: git.Repo) -> None:
    repo.create_tag("v1.0.0")
    repo.create_tag("v2.0.0")
    resolution = _resolve_git_version(repo, "")
    assert resolution.tracking_method == TRACKING_METHOD_VERSION
    assert resolution.version == "v2.0.0"


def test_explicit_version_tag(repo: git.Repo) -> None:
    repo.create_tag("v1.0.0")
    repo.create_tag("v2.0.0")
    resolution = _resolve_git_version(repo, "v1.0.0")
    assert resolution.tracking_method == TRACKING_METHOD_VERSION
    assert resolution.version == "v1.0.0"


def test_explicit_branch(repo: git.Repo) -> None:
    # Create branch in origin and fetch so it's visible as origin/feature.
    git.Repo(repo.remotes.origin.url).create_head("feature")
    repo.remotes.origin.fetch()
    repo.git.checkout("feature")
    resolution = _resolve_git_version(repo, "feature")
    assert resolution.tracking_method == TRACKING_METHOD_BRANCH
    assert resolution.version == "feature"


def test_explicit_commit_hash(repo: git.Repo) -> None:
    hexsha = repo.head.object.hexsha
    resolution = _resolve_git_version(repo, hexsha)
    assert resolution.tracking_method == TRACKING_METHOD_COMMIT
    assert resolution.current_hash == hexsha


def test_unknown_version_raises(repo: git.Repo) -> None:
    with pytest.raises(ValueError, match="nonexistent"):
        _resolve_git_version(repo, "nonexistent")


def test_resolution_captures_hash(repo: git.Repo) -> None:
    resolution = _resolve_git_version(repo, "")
    assert resolution.current_hash == repo.head.object.hexsha


def test_not_outdated_on_local_repo(repo: git.Repo) -> None:
    resolution = _resolve_git_version(repo, "")
    assert resolution.is_outdated is False


@pytest.fixture()
def pkg_dir(tmp_path: pathlib.Path) -> pathlib.Path:
    """A minimal package directory with a zkg.meta containing version = 1.0.0."""
    d = tmp_path / "mypkg"
    d.mkdir()
    (d / "zkg.meta").write_text("[package]\ndescription = test\nversion = 1.0.0\n")
    return d


@pytest.fixture()
def pkg_repo(tmp_path: pathlib.Path) -> git.Repo:
    """A minimal package repo with a zkg.meta and a v1.0.0 tag."""
    r = git.Repo.init(tmp_path / "pkg", initial_branch="main")
    r.config_writer().set_value("user", "name", "Test").release()
    r.config_writer().set_value("user", "email", "test@test").release()
    (tmp_path / "pkg" / "zkg.meta").write_text("[package]\ndescription = test\n")
    r.index.add(["zkg.meta"])
    r.index.commit("init")
    r.create_tag("v1.0.0")
    return r


def test_install_git_unknown_version(
    manager: Manager,
    pkg_repo: git.Repo,
) -> None:
    # Requesting a version tag that does not exist must fail.
    result = manager.install(str(pkg_repo.working_dir), "v99.0.0")
    assert result != ""


def test_install_git_missing_metadata(manager: Manager, repo: git.Repo) -> None:
    # A Git repo with no zkg.meta must fail with an error.
    result = manager.install(str(repo.working_dir))
    assert result != ""


def test_test_unknown_version(manager: Manager, pkg_repo: git.Repo) -> None:
    # manager.test() on a package with a non-existent version must return an error.
    error, passed, _ = manager.test(str(pkg_repo.working_dir), version="v99.0.0")
    assert not passed
    assert error != ""


@pytest.fixture()
def pkg_repo_with_test_command(tmp_path: pathlib.Path) -> git.Repo:
    """A minimal package repo with test_command in zkg.meta and a v1.0.0 tag."""
    r = git.Repo.init(tmp_path / "pkg", initial_branch="main")
    r.config_writer().set_value("user", "name", "Test").release()
    r.config_writer().set_value("user", "email", "test@test").release()
    (tmp_path / "pkg" / "zkg.meta").write_text(
        "[package]\ndescription = test\ntest_command = exit 0\n",
    )
    r.index.add(["zkg.meta"])
    r.index.commit("init")
    r.create_tag("v1.0.0")
    return r


def test_test_resolve_version_error(
    manager: Manager,
    pkg_repo_with_test_command: git.Repo,
) -> None:
    # When _resolve_git_version raises, manager.test() must return an error.
    with patch(
        "zeekpkg.manager._resolve_git_version",
        side_effect=ValueError("bad version"),
    ):
        error, passed, _ = manager.test(str(pkg_repo_with_test_command.working_dir))
    assert not passed
    assert "bad version" in error


def test__snapshot_from_git_repo(repo: git.Repo) -> None:
    meta_file = pathlib.Path(repo.working_dir) / "zkg.meta"
    meta_file.write_text("[package]\ndescription = test pkg\n")
    repo.index.add(["zkg.meta"])
    repo.index.commit("add zkg.meta")
    resolution = _resolve_git_version(repo, None)
    snapshot = _snapshot_from_git_repo(repo, resolution)
    assert isinstance(snapshot, PackageSnapshot)
    assert snapshot.meta["description"] == "test pkg"
    assert snapshot.version == resolution.version
    assert snapshot.tracking_method == resolution.tracking_method
    assert snapshot.current_hash == resolution.current_hash
    assert snapshot.working_dir == repo.working_dir


def test__snapshot_from_git_repo_missing_metadata_raises(repo: git.Repo) -> None:
    resolution = _resolve_git_version(repo, None)
    with pytest.raises(ValueError, match="missing"):
        _snapshot_from_git_repo(repo, resolution)


def _make_installed(
    manager: Manager,
    name: str,
    is_loaded: bool = False,
    is_pinned: bool = False,
    is_outdated: bool = False,
    tracking_method: str | None = None,
    current_version: str | None = None,
    current_hash: str | None = None,
) -> None:
    """Register a fake installed package in *manager*."""
    pkg = Package(git_url=f"https://example.com/{name}", name=name, canonical=True)
    status = PackageStatus(
        is_loaded=is_loaded,
        is_pinned=is_pinned,
        is_outdated=is_outdated,
        tracking_method=tracking_method,
        current_version=current_version,
        current_hash=current_hash,
    )
    manager.installed_pkgs[name] = InstalledPackage(pkg, status)


@pytest.mark.parametrize(
    "method,expected",
    [
        (TRACKING_METHOD_VERSION, True),
        (TRACKING_METHOD_BRANCH, True),
        (TRACKING_METHOD_COMMIT, True),
        (TRACKING_METHOD_BUILTIN, False),
        (None, False),
    ],
)
def test_is_git_package(method: str | None, expected: bool) -> None:
    assert _is_git_package(PackageStatus(tracking_method=method)) is expected


def test_info_from_clone(repo: git.Repo) -> None:
    # `_info_from_clone` must propagate metadata, versions, default branch,
    # and `version_type` from the snapshot and its arguments into `PackageInfo`.
    meta_file = pathlib.Path(repo.working_dir) / "zkg.meta"
    meta_file.write_text("[package]\ndescription = hello\n")
    repo.index.add(["zkg.meta"])
    repo.index.commit("add meta")
    repo.create_tag("v1.0.0")

    resolution = _resolve_git_version(repo, "v1.0.0")
    snapshot = _snapshot_from_git_repo(repo, resolution)
    package = Package(git_url=str(repo.working_dir), canonical=True)
    info = _info_from_clone(
        snapshot,
        package,
        status=None,
        versions=["v1.0.0"],
        default_branch="main",
        version_type=TRACKING_METHOD_VERSION,
    )
    assert isinstance(info, PackageInfo)
    assert info.metadata["description"] == "hello"
    assert info.versions == ["v1.0.0"]
    assert info.default_branch == "main"
    assert info.version_type == TRACKING_METHOD_VERSION
    assert info.invalid_reason == ""


def test_info_installed_missing_metadata(manager: Manager) -> None:
    # An installed package with no zkg.meta must be reported as invalid.
    pkg_name = "mypkg"
    pkg_dir = pathlib.Path(manager.package_clonedir) / pkg_name
    r = git.Repo.init(pkg_dir, initial_branch="main")
    r.config_writer().set_value("user", "name", "Test").release()
    r.config_writer().set_value("user", "email", "test@test").release()
    (pkg_dir / "file.txt").write_text("hi")
    r.index.add(["file.txt"])
    r.index.commit("init")

    _make_installed(
        manager,
        pkg_name,
        tracking_method=TRACKING_METHOD_BRANCH,
        current_version="main",
    )
    info = manager.info(f"https://example.com/{pkg_name}", prefer_installed=True)
    assert info.invalid_reason != ""
