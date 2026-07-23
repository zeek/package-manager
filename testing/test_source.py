import tempfile
from unittest.mock import MagicMock, patch

from zeekpkg.source import Source


class TestSourceCheckoutSkip:
    """Source.__init__ should skip git_checkout when HEAD is already at target."""

    def _make_mock_repo(self, branch_name: str) -> MagicMock:
        branch = MagicMock()
        branch.name = branch_name
        repo = MagicMock()
        repo.active_branch = branch
        repo.git.config.return_value = "https://example.com/repo.git"
        return repo

    def _make_detached_repo(self, hexsha: str) -> MagicMock:
        repo = MagicMock()
        type(repo.active_branch).name = property(
            lambda self: (_ for _ in ()).throw(TypeError("detached HEAD")),
        )
        type(repo).active_branch = property(
            lambda self: (_ for _ in ()).throw(TypeError("detached HEAD")),
        )
        repo.head.commit.hexsha = hexsha
        repo.git.config.return_value = "https://example.com/repo.git"
        return repo

    @patch("zeekpkg.source.git_checkout")
    @patch("zeekpkg.source.git_default_branch")
    @patch("zeekpkg.source.git.Repo")
    def test_skips_checkout_when_already_on_target(
        self,
        mock_repo_cls: MagicMock,
        mock_default_branch: MagicMock,
        mock_checkout: MagicMock,
    ) -> None:
        mock_repo_cls.return_value = self._make_mock_repo("main")
        mock_default_branch.return_value = "main"

        with tempfile.TemporaryDirectory() as tmp:
            Source("test", tmp, "https://example.com/repo.git")

        mock_checkout.assert_not_called()

    @patch("zeekpkg.source.git_checkout")
    @patch("zeekpkg.source.git_default_branch")
    @patch("zeekpkg.source.git.Repo")
    def test_checks_out_when_branch_differs(
        self,
        mock_repo_cls: MagicMock,
        mock_default_branch: MagicMock,
        mock_checkout: MagicMock,
    ) -> None:
        mock_repo_cls.return_value = self._make_mock_repo("old-branch")
        mock_default_branch.return_value = "main"

        with tempfile.TemporaryDirectory() as tmp:
            Source("test", tmp, "https://example.com/repo.git")

        mock_checkout.assert_called_once()

    @patch("zeekpkg.source.git_checkout")
    @patch("zeekpkg.source.git_default_branch")
    @patch("zeekpkg.source.git.Repo")
    def test_skips_checkout_explicit_version_matches_current(
        self,
        mock_repo_cls: MagicMock,
        mock_default_branch: MagicMock,
        mock_checkout: MagicMock,
    ) -> None:
        mock_repo_cls.return_value = self._make_mock_repo("v1.0")
        mock_default_branch.return_value = "main"

        with tempfile.TemporaryDirectory() as tmp:
            Source("test", tmp, "https://example.com/repo.git", version="v1.0")

        mock_checkout.assert_not_called()

    @patch("zeekpkg.source.git_checkout")
    @patch("zeekpkg.source.git_default_branch")
    @patch("zeekpkg.source.git.Repo")
    def test_checks_out_when_explicit_version_differs(
        self,
        mock_repo_cls: MagicMock,
        mock_default_branch: MagicMock,
        mock_checkout: MagicMock,
    ) -> None:
        mock_repo_cls.return_value = self._make_mock_repo("main")
        mock_default_branch.return_value = "main"

        with tempfile.TemporaryDirectory() as tmp:
            Source("test", tmp, "https://example.com/repo.git", version="v2.0")

        mock_checkout.assert_called_once()

    @patch("zeekpkg.source.git_checkout")
    @patch("zeekpkg.source.git_default_branch")
    @patch("zeekpkg.source.git.Repo")
    def test_skips_checkout_detached_head_matches_sha(
        self,
        mock_repo_cls: MagicMock,
        mock_default_branch: MagicMock,
        mock_checkout: MagicMock,
    ) -> None:
        sha = "abc123def456"
        mock_repo_cls.return_value = self._make_detached_repo(sha)
        mock_default_branch.return_value = "main"

        with tempfile.TemporaryDirectory() as tmp:
            Source("test", tmp, "https://example.com/repo.git", version=sha)

        mock_checkout.assert_not_called()
