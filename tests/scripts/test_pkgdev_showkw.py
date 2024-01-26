import textwrap
from functools import partial
from typing import List, NamedTuple
from unittest.mock import patch

import pytest
from snakeoil.contexts import chdir, os_environ

from pkgdev.scripts import run


class Profile(NamedTuple):
    """Profile record used to create profiles in a repository."""

    path: str
    arch: str
    status: str = "stable"
    deprecated: bool = False
    defaults: List[str] = None
    eapi: str = "5"


class TestPkgdevShowkwParseArgs:
    args = ("showkw", "--config", "no")

    def test_missing_target(self, capsys, tool):
        with pytest.raises(SystemExit):
            tool.parse_args(self.args)
        captured = capsys.readouterr()
        assert captured.err.strip() == (
            "pkgdev showkw: error: missing target argument and not in a supported repo"
        )

    def test_unknown_arches(self, capsys, tool, make_repo):
        repo = make_repo(arches=["amd64"])
        with pytest.raises(SystemExit):
            tool.parse_args([*self.args, "-a", "unknown", "-r", repo.location])
        captured = capsys.readouterr()
        assert captured.err.strip() == (
            "pkgdev showkw: error: unknown arch: 'unknown' (choices: amd64)"
        )

    def test_no_color(self, tool, make_repo, tmp_path):
        repo = make_repo(arches=["amd64"])
        repo.create_ebuild("foo/bar-0", keywords=("x86"))

        (config_file := tmp_path / "pkgcheck.conf").write_text(
            textwrap.dedent(
                """\
            [DEFAULT]
            showkw.color = true
        """
            )
        )

        def parse(*args):
            options, _ = tool.parse_args(
                ["showkw", "-r", repo.location, "foo/bar", "--config", str(config_file), *args]
            )
            return options

        with os_environ("NOCOLOR"):
            assert parse().color is True
        with os_environ(NOCOLOR="1"):
            # NOCOLOR overrides config file
            assert parse().color is False
            # cmd line option overrides NOCOLOR
            assert parse("--color", "n").color is False
            assert parse("--color", "y").color is True


class TestPkgdevShowkw:
    script = staticmethod(partial(run, "pkgdev"))
    base_args = ("pkgdev", "showkw", "--config", "n", "--color", "n")

    def _create_repo(self, make_repo):
        repo = make_repo(arches=["amd64", "ia64", "mips", "x86"])
        repo.create_profiles(
            [
                Profile("default/linux/amd64", "amd64"),
                Profile("default/linux/x86", "x86"),
                Profile("default/linux/ia64", "ia64", "dev"),
                Profile("default/linux/mips", "mips", "exp"),
            ]
        )
        return repo

    def _run_and_parse(self, capsys, *args):
        with (
            patch("sys.argv", [*self.base_args, "--format", "presto", *args]),
            pytest.raises(SystemExit) as excinfo,
        ):
            self.script()
        assert excinfo.value.code is None
        out, err = capsys.readouterr()
        assert not err
        lines = out.split("\n")
        table_columns = [s.strip() for s in lines[1].split("|")][1:]
        return {
            ver: dict(zip(table_columns, values))
            for ver, *values in map(lambda s: map(str.strip, s.split("|")), lines[3:-1])
        }

    def test_match(self, capsys, make_repo):
        repo = self._create_repo(make_repo)
        repo.create_ebuild("foo/bar-0")
        with (
            patch("sys.argv", [*self.base_args, "-r", repo.location, "foo/bar"]),
            pytest.raises(SystemExit) as excinfo,
        ):
            self.script()
        assert excinfo.value.code is None
        out, err = capsys.readouterr()
        assert not err
        assert out.split("\n")[0] == "keywords for foo/bar:"

    def test_match_short_name(self, capsys, make_repo):
        repo = self._create_repo(make_repo)
        repo.create_ebuild("foo/bar-0")
        with (
            patch("sys.argv", [*self.base_args, "-r", repo.location, "bar"]),
            pytest.raises(SystemExit) as excinfo,
        ):
            self.script()
        assert excinfo.value.code is None
        out, err = capsys.readouterr()
        assert not err
        assert out.split("\n")[0] == "keywords for foo/bar:"

    def test_match_cwd_repo(self, capsys, make_repo):
        repo = self._create_repo(make_repo)
        repo.create_ebuild("foo/bar-0")
        with (
            patch("sys.argv", [*self.base_args, "foo/bar"]),
            pytest.raises(SystemExit) as excinfo,
            chdir(repo.location),
        ):
            self.script()
        assert excinfo.value.code is None
        out, err = capsys.readouterr()
        assert not err
        assert out.split("\n")[0] == "keywords for foo/bar:"

    def test_match_cwd_pkg(self, capsys, make_repo):
        repo = self._create_repo(make_repo)
        repo.create_ebuild("foo/bar-0")
        with (
            patch("sys.argv", self.base_args),
            pytest.raises(SystemExit) as excinfo,
            chdir(repo.location + "/foo/bar"),
        ):
            self.script()
        assert excinfo.value.code is None
        _, err = capsys.readouterr()
        assert not err

    def test_no_matches(self, capsys, make_repo):
        repo = self._create_repo(make_repo)
        with (
            patch("sys.argv", [*self.base_args, "-r", repo.location, "foo/bar"]),
            pytest.raises(SystemExit) as excinfo,
        ):
            self.script()
        assert excinfo.value.code == 1
        out, err = capsys.readouterr()
        assert not out
        assert err.strip() == "pkgdev showkw: no matches for 'foo/bar'"

    def test_match_stable(self, capsys, make_repo):
        repo = self._create_repo(make_repo)
        repo.create_ebuild("foo/bar-0", keywords=("~amd64", "~ia64", "~mips", "x86"))
        res = self._run_and_parse(capsys, "-r", repo.location, "foo/bar", "--stable")
        assert set(res.keys()) == {"0"}
        assert {"amd64", "ia64", "mips", "x86"} & res["0"].keys() == {"amd64", "x86"}

    def test_match_unstable(self, capsys, make_repo):
        repo = self._create_repo(make_repo)
        repo.create_ebuild("foo/bar-0", keywords=("~amd64", "~ia64", "~mips", "x86"))
        res = self._run_and_parse(capsys, "-r", repo.location, "foo/bar", "--unstable")
        assert set(res.keys()) == {"0"}
        assert {"amd64", "ia64", "mips", "x86"} <= res["0"].keys()

    def test_match_specific_arch(self, capsys, make_repo):
        repo = self._create_repo(make_repo)
        repo.create_ebuild("foo/bar-0", keywords=("~amd64", "~ia64", "~mips", "x86"))
        res = self._run_and_parse(capsys, "-r", repo.location, "foo/bar", "--arch", "amd64")
        assert set(res.keys()) == {"0"}
        assert {"amd64", "ia64", "mips", "x86"} & res["0"].keys() == {"amd64"}

    def test_match_specific_multiple_arch(self, capsys, make_repo):
        repo = self._create_repo(make_repo)
        repo.create_ebuild("foo/bar-0", keywords=("~amd64", "~ia64", "~mips", "x86"))
        res = self._run_and_parse(capsys, "-r", repo.location, "foo/bar", "--arch", "amd64,mips")
        assert set(res.keys()) == {"0"}
        assert {"amd64", "ia64", "mips", "x86"} & res["0"].keys() == {"amd64", "mips"}

    def test_correct_keywords_status(self, capsys, make_repo):
        repo = self._create_repo(make_repo)
        repo.create_ebuild("foo/bar-0", keywords=("amd64", "~ia64", "~mips", "x86"))
        repo.create_ebuild("foo/bar-1", keywords=("~amd64", "-mips", "~x86"))
        repo.create_ebuild("foo/bar-2", keywords=("-*", "amd64", "-x86"), eapi=8, slot=2)
        res = self._run_and_parse(capsys, "-r", repo.location, "foo/bar")
        assert set(res.keys()) == {"0", "1", "2"}
        assert dict(amd64="+", ia64="~", mips="~", x86="+", slot="0").items() <= res["0"].items()
        assert dict(amd64="~", ia64="o", mips="-", x86="~", slot="0").items() <= res["1"].items()
        assert (
            dict(amd64="+", ia64="*", mips="*", x86="-", slot="2", eapi="8").items()
            <= res["2"].items()
        )

    @pytest.mark.parametrize(
        ("arg", "expected"),
        (
            pytest.param("--stable", {"amd64", "x86"}, id="stable"),
            pytest.param("--unstable", {"amd64", "ia64", "mips", "x86"}, id="unstable"),
            pytest.param("--only-unstable", {"ia64", "mips"}, id="only-unstable"),
        ),
    )
    def test_collapse(self, capsys, make_repo, arg, expected):
        repo = self._create_repo(make_repo)
        repo.create_ebuild("foo/bar-0", keywords=("amd64", "~ia64", "~mips", "~x86"))
        repo.create_ebuild("foo/bar-1", keywords=("~amd64", "~ia64", "~mips", "x86"))
        with (
            patch("sys.argv", [*self.base_args, "-r", repo.location, "foo/bar", "--collapse", arg]),
            pytest.raises(SystemExit) as excinfo,
        ):
            self.script()
        out, err = capsys.readouterr()
        assert excinfo.value.code is None
        assert not err
        arches = set(out.split("\n")[0].split())
        assert arches == expected
