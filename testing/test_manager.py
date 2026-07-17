"""Unit tests for zeekpkg.manager internals."""

import json
import os
import pathlib
from typing import ClassVar
from unittest.mock import MagicMock, patch

import git
import pytest

from zeekpkg.manager import GitResolution, Manager, _resolve_git_version
from zeekpkg.package import (
    TRACKING_METHOD_BRANCH,
    TRACKING_METHOD_COMMIT,
    TRACKING_METHOD_VERSION,
    Package,
    PackageInfo,
)


@pytest.fixture
def manager(tmp_path: pathlib.Path) -> Manager:
    with patch.object(Manager, "discover_builtin_packages", return_value=[]):
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


class TestResolveGitVersion:
    def test_defaults_to_branch_when_no_tags(self, repo: git.Repo) -> None:
        resolution = _resolve_git_version(repo, "")
        assert isinstance(resolution, GitResolution)
        assert resolution.tracking_method == TRACKING_METHOD_BRANCH
        assert resolution.version == "main"

    def test_defaults_to_latest_tag(self, repo: git.Repo) -> None:
        repo.create_tag("v1.0.0")
        repo.create_tag("v2.0.0")
        resolution = _resolve_git_version(repo, "")
        assert resolution.tracking_method == TRACKING_METHOD_VERSION
        assert resolution.version == "v2.0.0"

    def test_explicit_version_tag(self, repo: git.Repo) -> None:
        repo.create_tag("v1.0.0")
        repo.create_tag("v2.0.0")
        resolution = _resolve_git_version(repo, "v1.0.0")
        assert resolution.tracking_method == TRACKING_METHOD_VERSION
        assert resolution.version == "v1.0.0"

    def test_explicit_branch(self, repo: git.Repo) -> None:
        # Create branch in origin and fetch so it's visible as origin/feature.
        git.Repo(repo.remotes.origin.url).create_head("feature")
        repo.remotes.origin.fetch()
        repo.git.checkout("feature")
        resolution = _resolve_git_version(repo, "feature")
        assert resolution.tracking_method == TRACKING_METHOD_BRANCH
        assert resolution.version == "feature"

    def test_explicit_commit_hash(self, repo: git.Repo) -> None:
        hexsha = repo.head.object.hexsha
        resolution = _resolve_git_version(repo, hexsha)
        assert resolution.tracking_method == TRACKING_METHOD_COMMIT
        assert resolution.current_hash == hexsha

    def test_unknown_version_raises(self, repo: git.Repo) -> None:
        with pytest.raises(ValueError, match="nonexistent"):
            _resolve_git_version(repo, "nonexistent")

    def test_resolution_captures_hash(self, repo: git.Repo) -> None:
        resolution = _resolve_git_version(repo, "")
        assert resolution.current_hash == repo.head.object.hexsha

    def test_not_outdated_on_local_repo(self, repo: git.Repo) -> None:
        resolution = _resolve_git_version(repo, "")
        assert resolution.is_outdated is False


class TestManagerInstall:
    def test_git_unknown_version(
        self,
        manager: Manager,
        pkg_repo: git.Repo,
    ) -> None:
        # Requesting a version tag that does not exist must fail.
        result = manager.install(str(pkg_repo.working_dir), "v99.0.0")
        assert result != ""

    def test_git_missing_metadata(self, manager: Manager, repo: git.Repo) -> None:
        # A Git repo with no zkg.meta must fail with an error.
        result = manager.install(str(repo.working_dir))
        assert result != ""


class TestManagerTest:
    def test_unknown_version(self, manager: Manager, pkg_repo: git.Repo) -> None:
        # manager.test() on a package with a non-existent version must return an error.
        error, passed, _ = manager.test(str(pkg_repo.working_dir), version="v99.0.0")
        assert not passed
        assert error != ""

    def test_resolve_version_error(
        self,
        manager: Manager,
        pkg_repo_with_test_command: git.Repo,
    ) -> None:
        # When _resolve_git_version raises, manager.test() must return an error.
        with patch(
            "zeekpkg.manager._resolve_git_version",
            side_effect=ValueError("bad version"),
        ):
            error, passed, _ = manager.test(
                str(pkg_repo_with_test_command.working_dir),
            )
        assert not passed
        assert "bad version" in error


class TestInfoCache:
    """Manager.info() should return cached results on repeated calls."""

    def test_repeated_call_returns_same_object(self, manager: Manager) -> None:
        sentinel = PackageInfo(Package(git_url="https://example.com/pkg.git"))

        with patch.object(
            manager,
            "_info_lookup",
            return_value=sentinel,
        ) as mock_lookup:
            result1 = manager.info(
                "https://example.com/pkg.git",
                prefer_installed=False,
            )
            result2 = manager.info(
                "https://example.com/pkg.git",
                prefer_installed=False,
            )

        assert result1 is result2
        mock_lookup.assert_called_once()

    def test_different_args_call_lookup_separately(self, manager: Manager) -> None:
        sentinel_a = PackageInfo(Package(git_url="https://example.com/pkg.git"))
        sentinel_b = PackageInfo(Package(git_url="https://example.com/pkg.git"))

        with patch.object(
            manager,
            "_info_lookup",
            side_effect=[sentinel_a, sentinel_b],
        ) as mock_lookup:
            result_installed = manager.info(
                "https://example.com/pkg.git",
                prefer_installed=True,
            )
            result_fresh = manager.info(
                "https://example.com/pkg.git",
                prefer_installed=False,
            )

        assert result_installed is sentinel_a
        assert result_fresh is sentinel_b
        assert mock_lookup.call_count == 2


class TestBuildInfoDiskCache:
    """`discover_builtin_packages()` uses a disk cache keyed on zeek path, mtime, and size."""

    _FAKE_BUILD_INFO: ClassVar[dict[str, object]] = {
        "zkg": {"provides": [{"name": "spicy", "version": "1.0.0", "commit": "abc"}]},
    }
    _ZEEK_PATH: ClassVar[str] = "/usr/bin/zeek"
    _CACHE_KEY: ClassVar[dict[str, object]] = {
        "zeek_path": "/usr/bin/zeek",
        "zeek_mtime": 1234567890.0,
        "zeek_size": 4096,
    }

    def _mock_stat(self, mock_stat: MagicMock, key: dict[str, object]) -> None:
        mock_stat.return_value.st_mtime = key["zeek_mtime"]
        mock_stat.return_value.st_size = key["zeek_size"]

    def test_disk_cache_hit_skips_subprocess(self, manager: Manager) -> None:
        cache_file = os.path.join(manager.state_dir, "zeek_build_info_cache.json")

        with open(cache_file, "w") as f:
            json.dump({"key": self._CACHE_KEY, "build_info": self._FAKE_BUILD_INFO}, f)

        with (
            patch("zeekpkg.manager.get_zeek_info") as mock_zeek_info,
            patch("zeekpkg.manager.os.stat") as mock_stat,
            patch("zeekpkg.manager.subprocess.check_output") as mock_check_output,
        ):
            mock_zeek_info.return_value.zeek = self._ZEEK_PATH
            self._mock_stat(mock_stat, self._CACHE_KEY)
            manager._builtin_packages = None

            manager.discover_builtin_packages()

        mock_check_output.assert_not_called()

    def test_disk_cache_miss_runs_subprocess(self, manager: Manager) -> None:
        with (
            patch("zeekpkg.manager.get_zeek_info") as mock_zeek_info,
            patch("zeekpkg.manager.os.stat") as mock_stat,
            patch("zeekpkg.manager.subprocess.check_output") as mock_check_output,
        ):
            mock_zeek_info.return_value.zeek = self._ZEEK_PATH
            self._mock_stat(mock_stat, self._CACHE_KEY)
            mock_check_output.return_value = json.dumps(self._FAKE_BUILD_INFO).encode()
            manager._builtin_packages = None

            manager.discover_builtin_packages()

        mock_check_output.assert_called_once()

    def test_stale_mtime_reruns_subprocess(self, manager: Manager) -> None:
        cache_file = os.path.join(manager.state_dir, "zeek_build_info_cache.json")
        stale_key = {**self._CACHE_KEY, "zeek_mtime": 1000.0}

        with open(cache_file, "w") as f:
            json.dump({"key": stale_key, "build_info": self._FAKE_BUILD_INFO}, f)

        with (
            patch("zeekpkg.manager.get_zeek_info") as mock_zeek_info,
            patch("zeekpkg.manager.os.stat") as mock_stat,
            patch("zeekpkg.manager.subprocess.check_output") as mock_check_output,
        ):
            mock_zeek_info.return_value.zeek = self._ZEEK_PATH
            self._mock_stat(mock_stat, self._CACHE_KEY)
            mock_check_output.return_value = json.dumps(self._FAKE_BUILD_INFO).encode()
            manager._builtin_packages = None

            manager.discover_builtin_packages()

        mock_check_output.assert_called_once()

    def test_different_path_reruns_subprocess(self, manager: Manager) -> None:
        cache_file = os.path.join(manager.state_dir, "zeek_build_info_cache.json")
        other_key = {**self._CACHE_KEY, "zeek_path": "/opt/zeek/bin/zeek"}

        with open(cache_file, "w") as f:
            json.dump({"key": other_key, "build_info": self._FAKE_BUILD_INFO}, f)

        with (
            patch("zeekpkg.manager.get_zeek_info") as mock_zeek_info,
            patch("zeekpkg.manager.os.stat") as mock_stat,
            patch("zeekpkg.manager.subprocess.check_output") as mock_check_output,
        ):
            mock_zeek_info.return_value.zeek = self._ZEEK_PATH
            self._mock_stat(mock_stat, self._CACHE_KEY)
            mock_check_output.return_value = json.dumps(self._FAKE_BUILD_INFO).encode()
            manager._builtin_packages = None

            manager.discover_builtin_packages()

        mock_check_output.assert_called_once()
