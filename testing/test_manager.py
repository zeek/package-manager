import json
import os
from pathlib import Path
from typing import ClassVar
from unittest.mock import MagicMock, patch

import pytest

from zeekpkg.manager import Manager
from zeekpkg.package import Package, PackageInfo


@pytest.fixture
def manager(tmp_path: Path) -> Manager:
    with patch.object(Manager, "discover_builtin_packages", return_value=[]):
        return Manager(
            state_dir=str(tmp_path / "state"),
            script_dir=str(tmp_path / "scripts"),
            plugin_dir=str(tmp_path / "plugins"),
        )


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
