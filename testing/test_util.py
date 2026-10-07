"""Unit tests for zeekpkg._util."""

import pathlib

import git

from zeekpkg._util import git_branch_names


def test_git_branch_names(tmp_path: pathlib.Path) -> None:
    repo = git.Repo.init(tmp_path / "br-repo", initial_branch="main")
    repo.config_writer().set_value("user", "name", "Test").release()
    repo.config_writer().set_value("user", "email", "test@test").release()
    (tmp_path / "br-repo" / "f").write_text("x")
    repo.index.add(["f"])
    repo.index.commit("init")
    # Simulate remote tracking refs by creating refs/remotes/origin/main manually.
    repo.git.update_ref("refs/remotes/origin/main", "HEAD")
    branches = git_branch_names(repo)
    assert "main" in branches
