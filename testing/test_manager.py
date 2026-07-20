from pathlib import Path
from unittest.mock import patch

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
