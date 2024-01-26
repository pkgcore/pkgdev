import os
import textwrap
from functools import partial
from io import StringIO
from unittest.mock import patch

import pytest
from pkgdev.scripts import run
from snakeoil.contexts import chdir
from snakeoil.osutils import pjoin


class TestPkgdevPushParseArgs:
    def test_non_repo_cwd(self, capsys, tool):
        with pytest.raises(SystemExit):
            tool.parse_args(["push"])
        out, err = capsys.readouterr()
        assert err.strip() == "pkgdev push: error: not in ebuild repo"

    def test_non_git_repo_cwd(self, repo, capsys, tool):
        with pytest.raises(SystemExit), chdir(repo.location):
            tool.parse_args(["push"])
        out, err = capsys.readouterr()
        assert err.strip() == "pkgdev push: error: not in git repo"

    def test_non_ebuild_git_repo_cwd(self, make_repo, git_repo, capsys, tool):
        os.mkdir(pjoin(git_repo.path, "repo"))
        repo = make_repo(pjoin(git_repo.path, "repo"))
        with pytest.raises(SystemExit), chdir(repo.location):
            tool.parse_args(["push"])
        out, err = capsys.readouterr()
        assert err.strip() == "pkgdev push: error: not in ebuild git repo"

    def test_git_push_args_passthrough(self, repo, make_git_repo, tool):
        """Unknown arguments for ``pkgdev push`` are passed to ``git push``."""
        git_repo = make_git_repo(repo.location)
        with chdir(git_repo.path):
            options, _ = tool.parse_args(["push", "origin", "main"])
            assert options.push_args == ["origin", "main"]
            options, _ = tool.parse_args(["push", "-n", "--signed"])
            assert "--dry-run" in options.push_args
            assert "--signed" in options.push_args

    def test_scan_args(self, repo, make_git_repo, tool):
        git_repo = make_git_repo(repo.location)
        repo.create_ebuild("cat/pkg-0")
        git_repo.add_all("cat/pkg-0", commit=False)
        # pkgcheck isn't run in verbose mode by default
        with chdir(repo.location):
            options, _ = tool.parse_args(["commit"])
        assert "-v" not in options.scan_args
        # verbosity level is passed down to pkgcheck
        with chdir(repo.location):
            options, _ = tool.parse_args(["commit", "-v"])
        assert "-v" in options.scan_args


class TestPkgdevPush:
    script = staticmethod(partial(run, "pkgdev"))

    @pytest.fixture(autouse=True)
    def _setup(self, tmp_path, make_repo, make_git_repo):
        self.cache_dir = str(tmp_path / "cache")
        self.scan_args = [
            "--config",
            "no",
            "--pkgcheck-scan",
            f"--config no --cache-dir {self.cache_dir}",
        ]
        # args for running pkgdev like a script
        self.args = ["pkgdev", "push"] + self.scan_args

        # initialize parent repo
        self.parent_git_repo = make_git_repo(bare=True)
        # initialize child repo
        child_repo_path = tmp_path / "child-repo"
        child_repo_path.mkdir()
        self.child_git_repo = make_git_repo(str(child_repo_path))
        self.child_repo = make_repo(self.child_git_repo.path)
        self.child_git_repo.add_all("initial commit")
        # create a stub pkg and commit it
        self.child_repo.create_ebuild("cat/pkg-0")
        self.child_git_repo.add_all("cat/pkg-0")
        # set up parent repo as origin and push to it
        self.child_git_repo.run(["git", "remote", "add", "origin", self.parent_git_repo.path])
        self.child_git_repo.run(["git", "push", "-u", "origin", "main"])
        self.child_git_repo.run(["git", "remote", "set-head", "origin", "main"])

    def test_push(self, capsys):
        self.child_repo.create_ebuild("cat/pkg-1")
        self.child_git_repo.add_all("cat/pkg-1")

        with (
            patch("sys.argv", self.args),
            pytest.raises(SystemExit) as excinfo,
            chdir(self.child_git_repo.path),
        ):
            self.script()
        assert excinfo.value.code == 0

    def test_failed_push(self, capsys):
        self.child_repo.create_ebuild("cat/pkg-1", eapi="-1")
        self.child_git_repo.add_all("cat/pkg-1")

        # failed scans don't push commits
        with (
            patch("sys.argv", self.args),
            pytest.raises(SystemExit) as excinfo,
            chdir(self.child_git_repo.path),
        ):
            self.script()
        assert excinfo.value.code == 1
        out, err = capsys.readouterr()
        assert out == textwrap.dedent(
            """\
                cat/pkg
                  InvalidEapi: version 1: invalid EAPI '-1'

                FAILURES
                cat/pkg
                  InvalidEapi: version 1: invalid EAPI '-1'
            """
        )

        # but failures can be ignored to push anyway
        with (
            patch("sys.argv", self.args + ["--ask"]),
            patch("sys.stdin", StringIO("y\n")),
            pytest.raises(SystemExit) as excinfo,
            chdir(self.child_git_repo.path),
        ):
            self.script()
        assert excinfo.value.code == 0

    def test_warnings(self, capsys):
        pkgdir = os.path.dirname(self.child_repo.create_ebuild("cat/pkg-1"))
        os.makedirs((filesdir := pjoin(pkgdir, "files")), exist_ok=True)
        with open(pjoin(filesdir, "foo"), "w") as f:
            f.write("")
        self.child_git_repo.add_all("cat/pkg-1")

        # scans with warnings ask for confirmation before pushing with "--ask"
        with (
            patch("sys.argv", self.args + ["--ask"]),
            patch("sys.stdin", StringIO("n\n")),
            pytest.raises(SystemExit) as excinfo,
            chdir(self.child_git_repo.path),
        ):
            self.script()
        assert excinfo.value.code == 1
        out, err = capsys.readouterr()
        assert "EmptyFile" in out

        # but without "--ask" it still pushes
        with (
            patch("sys.argv", self.args),
            pytest.raises(SystemExit) as excinfo,
            chdir(self.child_git_repo.path),
        ):
            self.script()
        assert excinfo.value.code == 0
