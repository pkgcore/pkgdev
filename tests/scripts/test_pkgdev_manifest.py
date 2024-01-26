from functools import partial
from typing import List, Set
from unittest.mock import patch

import pytest
from pkgdev.scripts import run
from snakeoil.contexts import chdir
from snakeoil.osutils import pjoin


class TestPkgdevManifestParseArgs:
    def test_non_repo_cwd(self, capsys, tool):
        with pytest.raises(SystemExit) as excinfo:
            tool.parse_args(["manifest"])
        assert excinfo.value.code == 2
        out, err = capsys.readouterr()
        assert err.strip() == "pkgdev manifest: error: not in ebuild repo"

    @pytest.mark.skip
    def test_repo_cwd(self, repo, capsys, tool):
        repo.create_ebuild("cat/pkg-0")
        with chdir(repo.location):
            options, _ = tool.parse_args(["manifest"])
        matches = [x.cpvstr for x in repo.itermatch(options.restriction)]
        assert matches == ["cat/pkg-0"]

    def test_repo_relative_pkg(self, repo, capsys, tool):
        repo.create_ebuild("cat/pkg-0")
        repo.create_ebuild("cat/newpkg-0")
        with chdir(pjoin(repo.location, "cat/pkg")):
            options, _ = tool.parse_args(["manifest", "."])
        matches = [x.cpvstr for x in repo.itermatch(options.restriction)]
        assert matches == ["cat/pkg-0"]

    @pytest.mark.skip
    def test_repo_relative_category(self, repo, capsys, tool):
        repo.create_ebuild("cat/pkg-0")
        repo.create_ebuild("cat/newpkg-0")

        with chdir(pjoin(repo.location, "cat")):
            options, _ = tool.parse_args(["manifest", "pkg"])
        matches = [x.cpvstr for x in repo.itermatch(options.restriction)]
        assert matches == ["cat/pkg-0"]

        with chdir(pjoin(repo.location, "cat")):
            options, _ = tool.parse_args(["manifest", "."])
        matches = [x.cpvstr for x in repo.itermatch(options.restriction)]
        assert set(matches) == {"cat/pkg-0", "cat/newpkg-0"}

    def test_repo_relative_outside(self, tmp_path, repo, capsys, tool):
        repo.create_ebuild("cat/pkg-0")
        (ebuild := tmp_path / "pkg.ebuild").touch()
        with pytest.raises(SystemExit) as excinfo:
            with chdir(repo.location):
                tool.parse_args(["manifest", str(ebuild)])
        assert excinfo.value.code == 2
        out, err = capsys.readouterr()
        assert (
            err.strip()
            == f"pkgdev manifest: error: {repo.repo_id!r} repo doesn't contain: {str(ebuild)!r}"
        )

    @pytest.mark.skip
    def test_dir_target(self, repo, capsys, tool):
        repo.create_ebuild("cat/pkg-0")
        with chdir(repo.location):
            options, _ = tool.parse_args(["manifest", pjoin(repo.location, "cat")])
        matches = [x.cpvstr for x in repo.itermatch(options.restriction)]
        assert matches == ["cat/pkg-0"]

    def test_ebuild_target(self, repo, capsys, tool):
        path = repo.create_ebuild("cat/pkg-0")
        with chdir(repo.location):
            options, _ = tool.parse_args(["manifest", path])
        matches = [x.cpvstr for x in repo.itermatch(options.restriction)]
        assert matches == ["cat/pkg-0"]

    def test_atom_target(self, repo, capsys, tool):
        repo.create_ebuild("cat/pkg-0")
        with chdir(repo.location):
            options, _ = tool.parse_args(["manifest", "cat/pkg"])
        matches = [x.cpvstr for x in repo.itermatch(options.restriction)]
        assert matches == ["cat/pkg-0"]

    def test_if_modified_target(self, repo, make_git_repo, tool):
        def manifest_matches() -> Set[str]:
            repo.sync()
            with chdir(repo.location):
                options, _ = tool.parse_args(["manifest", "--if-modified"])
            return {x.cpvstr for x in repo.itermatch(options.restriction)}

        git_repo = make_git_repo(repo.location)
        repo.create_ebuild("cat/oldpkg-0")
        git_repo.add_all("cat/oldpkg-0")

        # New package
        repo.create_ebuild("cat/newpkg-0")
        assert manifest_matches() == {"cat/newpkg-0"}
        git_repo.add_all("cat/newpkg-0")

        # Untracked file
        ebuild_path = repo.create_ebuild("cat/newpkg-1")
        assert manifest_matches() == {"cat/newpkg-1"}

        # Staged file
        git_repo.add(ebuild_path, commit=False)
        assert manifest_matches() == {"cat/newpkg-1"}

        # No modified files
        git_repo.add_all("cat/newpkg-1")
        assert manifest_matches() == set()

        # Modified file
        ebuild_path = repo.create_ebuild("cat/newpkg-1", eapi=8)
        assert manifest_matches() == {"cat/newpkg-1"}
        git_repo.add_all("cat/newpkg-1: eapi 8")

        # Renamed file
        git_repo.remove(ebuild_path, commit=False)
        ebuild_path = repo.create_ebuild("cat/newpkg-2")
        git_repo.add(ebuild_path, commit=False)
        assert manifest_matches() == {"cat/newpkg-2"}
        git_repo.add_all("cat/newpkg-2: rename")

        # Deleted file
        git_repo.remove(ebuild_path, commit=False)
        assert manifest_matches() == set()

        # Deleted package
        ebuild_path = repo.create_ebuild("cat/newpkg-0")
        git_repo.remove(ebuild_path, commit=False)
        assert manifest_matches() == set()

    @pytest.mark.skip
    def test_ignore_fetch_restricted(self, repo, tool):
        def manifest_matches() -> List[str]:
            with chdir(repo.location):
                options, _ = tool.parse_args(["manifest", "--ignore-fetch-restricted"])
            return [x.cpvstr for x in repo.itermatch(options.restriction)]

        # No RESTRICT
        repo.create_ebuild("cat/pkg-0")
        assert manifest_matches() == ["cat/pkg-0"]

        # Not fetch RESTRICT
        repo.create_ebuild("cat/pkg-0", restrict=("mirror"))
        assert manifest_matches() == ["cat/pkg-0"]

        # fetch RESTRICT
        repo.create_ebuild("cat/pkg-0", restrict=("fetch"))
        assert manifest_matches() == []

        # Multiple RESTRICT
        repo.create_ebuild("cat/pkg-0", restrict=("mirror", "fetch"))
        assert manifest_matches() == []

    def test_non_repo_dir_target(self, tmp_path, repo, capsys, tool):
        with pytest.raises(SystemExit) as excinfo, chdir(repo.location):
            tool.parse_args(["manifest", str(tmp_path)])
        assert excinfo.value.code == 2
        out, err = capsys.readouterr()
        assert err.startswith("pkgdev manifest: error: 'fake' repo doesn't contain:")

    def test_invalid_atom_target(self, repo, capsys, tool):
        with pytest.raises(SystemExit) as excinfo, chdir(repo.location):
            tool.parse_args(["manifest", "=cat/pkg"])
        assert excinfo.value.code == 2
        out, err = capsys.readouterr()
        assert err.startswith("pkgdev manifest: error: invalid atom: '=cat/pkg'")


class TestPkgdevManifest:
    script = staticmethod(partial(run, "pkgdev"))

    @pytest.fixture(autouse=True)
    def _setup(self):
        self.args = ["pkgdev", "manifest"]

    def test_good_manifest(self, capsys, repo):
        repo.create_ebuild("cat/pkg-0")
        with (
            patch("sys.argv", self.args),
            pytest.raises(SystemExit) as excinfo,
            chdir(repo.location),
        ):
            self.script()
        assert excinfo.value.code == 0
        out, err = capsys.readouterr()
        assert out == err == ""

    def test_bad_manifest(self, capsys, repo):
        repo.create_ebuild("cat/pkg-0")
        repo.create_ebuild("cat/pkg-1", eapi="-1")
        with (
            patch("sys.argv", self.args),
            pytest.raises(SystemExit) as excinfo,
            chdir(repo.location),
        ):
            self.script()
        assert excinfo.value.code == 1
        out, err = capsys.readouterr()
        assert not err
        assert out == " * cat/pkg-1: invalid EAPI '-1'\n"
