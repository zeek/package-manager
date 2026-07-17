"""Unit tests for zeekpkg.manager internals."""

import pathlib
from unittest.mock import patch

import git
import pytest

from zeekpkg.manager import (
    GitResolution,
    Manager,
    _info_from_snapshot,
    _is_directory_package,
    _is_git_package,
    _prepare_snapshot,
    _resolve_git_version,
    _snapshot_from_directory,
    _snapshot_from_git_repo,
)
from zeekpkg.package import (
    InstalledPackage,
    Package,
    PackageInfo,
    PackageSnapshot,
    PackageStatus,
    TrackingMethod,
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
    assert resolution.tracking_method == TrackingMethod.BRANCH
    assert resolution.version == "main"


def test_defaults_to_latest_tag(repo: git.Repo) -> None:
    repo.create_tag("v1.0.0")
    repo.create_tag("v2.0.0")
    resolution = _resolve_git_version(repo, "")
    assert resolution.tracking_method == TrackingMethod.VERSION
    assert resolution.version == "v2.0.0"


def test_explicit_version_tag(repo: git.Repo) -> None:
    repo.create_tag("v1.0.0")
    repo.create_tag("v2.0.0")
    resolution = _resolve_git_version(repo, "v1.0.0")
    assert resolution.tracking_method == TrackingMethod.VERSION
    assert resolution.version == "v1.0.0"


def test_explicit_branch(repo: git.Repo) -> None:
    # Create branch in origin and fetch so it's visible as origin/feature.
    git.Repo(repo.remotes.origin.url).create_head("feature")
    repo.remotes.origin.fetch()
    repo.git.checkout("feature")
    resolution = _resolve_git_version(repo, "feature")
    assert resolution.tracking_method == TrackingMethod.BRANCH
    assert resolution.version == "feature"


def test_explicit_commit_hash(repo: git.Repo) -> None:
    hexsha = repo.head.object.hexsha
    resolution = _resolve_git_version(repo, hexsha)
    assert resolution.tracking_method == TrackingMethod.COMMIT
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
        (TrackingMethod.VERSION, True),
        (TrackingMethod.BRANCH, True),
        (TrackingMethod.COMMIT, True),
        (TrackingMethod.BUILTIN, False),
        (TrackingMethod.DIRECTORY, False),
        (None, False),
    ],
)
def test_is_git_package(method: str | None, expected: bool) -> None:
    assert _is_git_package(PackageStatus(tracking_method=method)) is expected


def test_snapshot_from_directory(pkg_dir: pathlib.Path) -> None:
    snapshot = _snapshot_from_directory(str(pkg_dir))
    assert isinstance(snapshot, PackageSnapshot)
    assert snapshot.version == "1.0.0"
    assert snapshot.meta["description"] == "test"
    assert snapshot.working_dir == str(pkg_dir)


def test_snapshot_from_directory_missing_metadata_raises(
    tmp_path: pathlib.Path,
) -> None:
    with pytest.raises(ValueError, match="missing"):
        _snapshot_from_directory(str(tmp_path))


def test_snapshot_from_directory_missing_version_raises(tmp_path: pathlib.Path) -> None:
    (tmp_path / "zkg.meta").write_text("[package]\ndescription = test\n")
    with pytest.raises(ValueError, match="version"):
        _snapshot_from_directory(str(tmp_path))


def test_is_directory_package(pkg_dir: pathlib.Path, repo: git.Repo) -> None:
    # A plain directory without .git is a directory package; a git repo is not.
    assert _is_directory_package(str(pkg_dir)) is True
    assert _is_directory_package(str(repo.working_dir)) is False


def test_prepare_snapshot_directory(
    pkg_dir: pathlib.Path,
    tmp_path: pathlib.Path,
) -> None:
    # _prepare_snapshot on a plain directory returns a directory-backed snapshot.
    package = Package(git_url=str(pkg_dir), canonical=True)
    snapshot = _prepare_snapshot(package, None, str(tmp_path / "dest"))
    assert snapshot.version == "1.0.0"
    assert snapshot.tracking_method == TrackingMethod.DIRECTORY
    assert snapshot.current_hash is None
    assert snapshot.is_outdated is False


def test_prepare_snapshot_git(repo: git.Repo, tmp_path: pathlib.Path) -> None:
    # _prepare_snapshot on a Git repo resolves version and tracking method.
    meta_file = pathlib.Path(repo.working_dir) / "zkg.meta"
    meta_file.write_text("[package]\ndescription = test\n")
    repo.index.add(["zkg.meta"])
    repo.index.commit("add meta")
    # Point at the clone directly, passing it as existing_clone to avoid a
    # network clone.
    package = Package(git_url=str(repo.working_dir), canonical=True)
    snapshot = _prepare_snapshot(
        package,
        None,
        str(tmp_path / "dest"),
        existing_clone=repo,
    )
    assert snapshot.tracking_method == TrackingMethod.BRANCH
    assert snapshot.current_hash is not None


def test_info_directory_backend(manager: Manager, pkg_dir: pathlib.Path) -> None:
    # manager.info() on a plain directory must return valid package info.
    info = manager.info(str(pkg_dir))
    assert info.invalid_reason == ""
    assert info.metadata_version == "1.0.0"
    assert info.version_type == TrackingMethod.DIRECTORY


def test_install_directory_backend(manager: Manager, pkg_dir: pathlib.Path) -> None:
    result = manager.install(str(pkg_dir))
    assert result == ""
    ipkg = manager.find_installed_package("mypkg")
    assert ipkg is not None
    assert ipkg.status.tracking_method == TrackingMethod.DIRECTORY
    assert ipkg.status.current_version == "1.0.0"


def test_install_directory_missing_metadata(
    manager: Manager,
    tmp_path: pathlib.Path,
) -> None:
    # A plain directory with no zkg.meta must fail with an error.
    pkg_dir = tmp_path / "mypkg"
    pkg_dir.mkdir()
    result = manager.install(str(pkg_dir))
    assert result != ""


def test_info_from_snapshot(repo: git.Repo) -> None:
    # `_info_from_snapshot` must propagate metadata, versions, default branch,
    # and `version_type` from the snapshot and its arguments into `PackageInfo`.
    meta_file = pathlib.Path(repo.working_dir) / "zkg.meta"
    meta_file.write_text("[package]\ndescription = hello\n")
    repo.index.add(["zkg.meta"])
    repo.index.commit("add meta")
    repo.create_tag("v1.0.0")

    resolution = _resolve_git_version(repo, "v1.0.0")
    snapshot = _snapshot_from_git_repo(repo, resolution)
    package = Package(git_url=str(repo.working_dir), canonical=True)
    info = _info_from_snapshot(
        snapshot,
        package,
        status=None,
        versions=["v1.0.0"],
        default_branch="main",
        version_type=TrackingMethod.VERSION,
    )
    assert isinstance(info, PackageInfo)
    assert info.metadata["description"] == "hello"
    assert info.versions == ["v1.0.0"]
    assert info.default_branch == "main"
    assert info.version_type == TrackingMethod.VERSION
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
        tracking_method=TrackingMethod.BRANCH,
        current_version="main",
    )
    info = manager.info(f"https://example.com/{pkg_name}", prefer_installed=True)
    assert info.invalid_reason != ""


def test_refresh_skips_non_git_packages(manager: Manager) -> None:
    # A directory package must not trigger _open_package_clone (which would
    # fail with NoSuchPathError).
    _make_installed(
        manager,
        "mypkg",
        tracking_method=TrackingMethod.DIRECTORY,
        current_version="1.0.0",
    )
    # Should complete without raising.
    manager.refresh_installed_packages()


def test_upgrade_not_installed(manager: Manager) -> None:
    assert manager.upgrade("nonexistent") == "no such package installed"


def test_upgrade_pinned(manager: Manager) -> None:
    _make_installed(manager, "mypkg", is_pinned=True, is_outdated=True)
    assert manager.upgrade("https://example.com/mypkg") == "package is pinned"


def test_upgrade_not_outdated(manager: Manager) -> None:
    _make_installed(manager, "mypkg", is_outdated=False)
    assert manager.upgrade("https://example.com/mypkg") == "package is not outdated"


def test_package_versions(manager: Manager) -> None:
    pkg_dir = pathlib.Path(manager.package_clonedir) / "mypkg"
    r = git.Repo.init(pkg_dir)
    r.config_writer().set_value("user", "name", "Test").release()
    r.config_writer().set_value("user", "email", "test@test").release()
    (pkg_dir / "file.txt").write_text("hi")
    r.index.add(["file.txt"])
    r.index.commit("init")
    r.create_tag("v1.0.0")
    r.create_tag("v2.0.0")

    package = Package(git_url=str(pkg_dir), name="mypkg", canonical=True)
    ipkg = InstalledPackage(package, PackageStatus())
    assert manager.package_versions(ipkg) == ["v1.0.0", "v2.0.0"]


def test_open_package_clone(manager: Manager) -> None:
    # Create a bare git repo in the manager's package clone directory.
    pkg_dir = pathlib.Path(manager.package_clonedir) / "mypkg"
    r = git.Repo.init(pkg_dir)
    r.config_writer().set_value("user", "name", "Test").release()
    r.config_writer().set_value("user", "email", "test@test").release()
    (pkg_dir / "file.txt").write_text("hi")
    r.index.add(["file.txt"])
    r.index.commit("init")

    package = Package(git_url=str(pkg_dir), name="mypkg", canonical=True)
    clone = manager._open_package_clone(package)
    assert isinstance(clone, git.Repo)
    assert clone.working_dir == str(pkg_dir)


@pytest.mark.parametrize(
    "skip,expected",
    [
        (False, "does not match"),
        (True, ""),
    ],
    ids=["fails", "skip_validation"],
)
def test_install_version_field_mismatch(
    manager: Manager,
    pkg_repo: git.Repo,
    skip: bool,
    expected: str,
) -> None:
    # A zkg.meta version field that does not match the Git tag must fail;
    # with skip_version_validation the mismatch is only a warning.
    (pathlib.Path(pkg_repo.working_dir) / "zkg.meta").write_text(
        "[package]\ndescription = test\nversion = v9.9.9\n",
    )
    pkg_repo.index.add(["zkg.meta"])
    pkg_repo.index.commit("wrong version")
    pkg_repo.create_tag("v1.0.1")
    result = manager.install(
        str(pkg_repo.working_dir),
        "v1.0.1",
        skip_version_validation=skip,
    )
    assert expected in result


def test_install_no_version_field_passes(manager: Manager, pkg_repo: git.Repo) -> None:
    # No version field in zkg.meta is fine, validation only applies when the field is present.
    result = manager.install(str(pkg_repo.working_dir), "v1.0.0")
    assert result == ""


def test_bundle_skips_non_git_existing_clone(
    manager: Manager,
    tmp_path: pathlib.Path,
) -> None:
    # A directory package must not be used as an existing clone source;
    # bundle() should fall through to cloning from the URL (which will fail
    # for a bogus URL, but must not crash trying to open a git.Repo).
    _make_installed(
        manager,
        "mypkg",
        tracking_method=TrackingMethod.DIRECTORY,
        current_version="1.0.0",
    )
    bundle_file = str(tmp_path / "out.tar.gz")
    result = manager.bundle(
        bundle_file,
        [("https://example.com/mypkg", "1.0.0")],
        prefer_existing_clones=True,
    )
    # Fails because the URL is not a real git repo, not because of a git.Repo crash.
    assert "failed to clone" in result


def test_bundle_directory_package(
    manager: Manager,
    pkg_dir: pathlib.Path,
    tmp_path: pathlib.Path,
) -> None:
    # bundle() must report that bundling is unsupported for directory packages.
    bundle_file = str(tmp_path / "out.tar.gz")
    result = manager.bundle(
        bundle_file,
        [(str(pkg_dir), "")],
    )
    assert "cannot bundle directory package" in result


def test_upgrade_directory_package(
    manager: Manager,
    pkg_dir: pathlib.Path,
) -> None:
    # A directory package is never outdated, so upgrade must be a no-op.
    assert manager.install(str(pkg_dir)) == ""
    assert manager.upgrade(str(pkg_dir)) == "package is not outdated"


def test_test_directory_package(
    manager: Manager,
    pkg_dir: pathlib.Path,
) -> None:
    # test() on a directory package without a test_command must report the
    # absence as an error.
    error, passed, _ = manager.test(str(pkg_dir))
    assert not passed
    assert "test_command" in error
