import subprocess
from unittest.mock import patch

import pytest
from snakeoil.cli.exceptions import UserException
from snakeoil.contexts import chdir
from pkgdev import git


class TestGitRun:
    def test_git_missing(self):
        with patch("subprocess.run") as git_run:
            git_run.side_effect = FileNotFoundError("no such file 'git'")
            with pytest.raises(UserException, match="no such file 'git'"):
                git.run("commit")

    def test_failed_run(self):
        with patch("subprocess.run") as git_run:
            git_run.side_effect = subprocess.CalledProcessError(1, "git commit")
            with pytest.raises(git.GitError):
                git.run("commit")

    def test_successful_run(self, git_repo):
        with chdir(git_repo.path):
            p = git.run("rev-parse", "--abbrev-ref", "HEAD", stdout=subprocess.PIPE)
        assert p.stdout.strip() == "main"
