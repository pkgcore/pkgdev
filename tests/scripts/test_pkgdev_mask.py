import os
import sys
import textwrap
from datetime import datetime, timedelta, timezone
from functools import partial
from pathlib import Path
from unittest.mock import patch

import pytest
from pkgcore.ebuild.atom import atom as atom_cls
from pkgdev.scripts import run
from snakeoil.contexts import chdir, os_environ
from snakeoil.osutils import pjoin


class TestPkgdevMaskParseArgs:
    def test_non_repo_cwd(self, capsys, tool):
        with pytest.raises(SystemExit):
            tool.parse_args(["mask"])
        out, err = capsys.readouterr()
        assert err.strip() == "pkgdev mask: error: not in ebuild repo"

    def test_non_git_repo_cwd(self, repo, capsys, tool):
        with pytest.raises(SystemExit), chdir(repo.location):
            tool.parse_args(["mask"])
        out, err = capsys.readouterr()
        assert err.strip() == "pkgdev mask: error: not in git repo"

    def test_non_ebuild_git_repo_cwd(self, make_repo, git_repo, capsys, tool):
        os.mkdir(pjoin(git_repo.path, "repo"))
        repo = make_repo(pjoin(git_repo.path, "repo"))
        with pytest.raises(SystemExit), chdir(repo.location):
            tool.parse_args(["mask"])
        out, err = capsys.readouterr()
        assert err.strip() == "pkgdev mask: error: not in ebuild git repo"

    def test_cwd_target(self, repo, make_git_repo, capsys, tool):
        git_repo = make_git_repo(repo.location)
        # empty repo
        with pytest.raises(SystemExit), chdir(repo.location):
            tool.parse_args(["mask"])
        out, err = capsys.readouterr()
        assert err.strip() == "pkgdev mask: error: not in a package directory"

        # not in package dir
        repo.create_ebuild("cat/pkg-0")
        git_repo.add_all("cat/pkg-0")
        with pytest.raises(SystemExit), chdir(repo.location):
            tool.parse_args(["mask"])
        out, err = capsys.readouterr()
        assert err.strip() == "pkgdev mask: error: not in a package directory"

        # masking CWD package
        with chdir(pjoin(repo.location, "cat/pkg")):
            options, _ = tool.parse_args(["mask"])
        assert options.atoms == [atom_cls("cat/pkg")]

    def test_targets(self, repo, make_git_repo, capsys, tool):
        git_repo = make_git_repo(repo.location)

        # invalid atom
        with pytest.raises(SystemExit), chdir(repo.location):
            tool.parse_args(["mask", "pkg"])
        out, err = capsys.readouterr()
        assert err.strip() == "pkgdev mask: error: invalid atom: 'pkg'"

        # nonexistent pkg
        with pytest.raises(SystemExit), chdir(repo.location):
            tool.parse_args(["mask", "cat/nonexistent"])
        out, err = capsys.readouterr()
        assert err.strip() == "pkgdev mask: error: no repo matches: 'cat/nonexistent'"

        # masked pkg
        repo.create_ebuild("cat/pkg-0")
        git_repo.add_all("cat/pkg-0")
        with chdir(repo.location):
            options, _ = tool.parse_args(["mask", "cat/pkg"])
        assert options.atoms == [atom_cls("cat/pkg")]

    def test_email_not_rites(self, repo, make_git_repo, capsys, tool):
        git_repo = make_git_repo(repo.location)

        # masked pkg
        repo.create_ebuild("cat/pkg-0")
        git_repo.add_all("cat/pkg-0")
        with pytest.raises(SystemExit), chdir(repo.location):
            tool.parse_args(["mask", "--email", "cat/pkg"])
        _, err = capsys.readouterr()
        assert err.strip() == "pkgdev mask: error: last rites required for email support"


class TestPkgdevMask:
    script = staticmethod(partial(run, "pkgdev"))

    @pytest.fixture(autouse=True)
    def _setup(self, make_repo, make_git_repo):
        # args for running pkgdev like a script
        self.args = ["pkgdev", "mask"]
        self.repo = make_repo(arches=["amd64"])
        self.git_repo = make_git_repo(self.repo.location)
        self.today = datetime.now(timezone.utc)

        # add stub pkg
        self.repo.create_ebuild("cat/pkg-0")
        self.git_repo.add_all("cat/pkg-0")

        # create profile
        self.profile_path = pjoin(self.repo.location, "profiles/arch/amd64")
        os.makedirs(self.profile_path)
        with open(pjoin(self.repo.location, "profiles/profiles.desc"), "w") as f:
            f.write("amd64 arch/amd64 stable\n")

        self.masks_path = Path(pjoin(self.repo.location, "profiles/package.mask"))

    @property
    def profile(self):
        profile = list(self.repo.config.profiles)[0]
        return self.repo.config.profiles.create_profile(profile)

    def test_empty_repo(self):
        assert self.profile.masks == frozenset()

    def test_nonexistent_editor(self, capsys):
        with (
            os_environ("VISUAL", EDITOR="12345"),
            patch("sys.argv", self.args + ["cat/pkg"]),
            pytest.raises(SystemExit),
            chdir(pjoin(self.repo.path)),
        ):
            self.script()
        out, err = capsys.readouterr()
        assert err.strip() == "pkgdev mask: error: nonexistent editor: '12345'"

    def test_nonexistent_visual(self, capsys):
        with (
            os_environ("EDITOR", VISUAL="12345"),
            patch("sys.argv", self.args + ["cat/pkg"]),
            pytest.raises(SystemExit),
            chdir(pjoin(self.repo.path)),
        ):
            self.script()
        out, err = capsys.readouterr()
        assert err.strip() == "pkgdev mask: error: nonexistent editor: '12345'"

    def test_failed_editor(self, capsys):
        with (
            os_environ("VISUAL", EDITOR="sed -i 's///'"),
            patch("sys.argv", self.args + ["cat/pkg"]),
            pytest.raises(SystemExit),
            chdir(pjoin(self.repo.path)),
        ):
            self.script()
        out, err = capsys.readouterr()
        assert err.strip() == "pkgdev mask: error: failed writing mask comment"

    def test_empty_mask_comment(self, capsys):
        with (
            os_environ("VISUAL", EDITOR="sed -i 's/#/#/'"),
            patch("sys.argv", self.args + ["cat/pkg"]),
            pytest.raises(SystemExit),
            chdir(pjoin(self.repo.path)),
        ):
            self.script()
        out, err = capsys.readouterr()
        assert err.strip() == "pkgdev mask: error: empty mask comment"

    def test_mask_cwd(self):
        with (
            os_environ("VISUAL", EDITOR="sed -i '1s/$/mask comment/'"),
            patch("sys.argv", self.args),
            pytest.raises(SystemExit),
            chdir(pjoin(self.repo.path, "cat/pkg")),
        ):
            self.script()
        assert self.profile.masks == frozenset([atom_cls("cat/pkg")])

    def test_mask_target(self):
        with (
            os_environ("VISUAL", EDITOR="sed -i '1s/$/mask comment/'"),
            patch("sys.argv", self.args + ["cat/pkg"]),
            pytest.raises(SystemExit),
            chdir(pjoin(self.repo.path)),
        ):
            self.script()
        assert self.profile.masks == frozenset([atom_cls("cat/pkg")])

    def test_mask_ebuild_path(self):
        with (
            os_environ("VISUAL", EDITOR="sed -i '1s/$/mask comment/'"),
            patch("sys.argv", self.args + ["cat/pkg/pkg-0.ebuild"]),
            pytest.raises(SystemExit),
            chdir(pjoin(self.repo.path)),
        ):
            self.script()
        assert self.profile.masks == frozenset([atom_cls("=cat/pkg-0")])

    def test_existing_masks(self):
        self.masks_path.write_text(
            textwrap.dedent(
                """\
                    # Random Dev <random.dev@email.com> (2021-03-24)
                    # masked
                    cat/masked
                """
            )
        )

        with (
            os_environ("VISUAL", EDITOR="sed -i '1s/$/mask comment/'"),
            patch("sys.argv", self.args + ["=cat/pkg-0"]),
            pytest.raises(SystemExit),
            chdir(pjoin(self.repo.path)),
        ):
            self.script()
        assert self.profile.masks == frozenset([atom_cls("cat/masked"), atom_cls("=cat/pkg-0")])

    def test_invalid_header(self, capsys):
        self.masks_path.write_text(
            textwrap.dedent(
                """\
                    # Random Dev <random.dev@email.com> (2022-09-09)
                    #
                    # Larry the Cow was here
                    #
                    # masked
                    cat/masked

                    # Larry the Cow <larry@gentoo.org> (2022-09-09)
                    #test
                    # Larry the Cow wasn't here
                    cat/masked2
                """
            )
        )

        with (
            os_environ("VISUAL", EDITOR="sed -i '1s/$/mask comment/'"),
            patch("sys.argv", self.args + ["=cat/pkg-0"]),
            pytest.raises(SystemExit),
            chdir(pjoin(self.repo.path)),
        ):
            self.script()
        _, err = capsys.readouterr()
        assert "invalid mask entry header, lineno 9" in err

    def test_invalid_author(self, capsys):
        for line in (
            "# Random Dev <random.dev@email.com>",
            "# Random Dev <random.dev@email.com) (2021-03-24)",
            "# Random Dev (2021-03-24)",
            "# Random Dev <random.dev@email.com> 2021-03-24",
            "# Random Dev <random.dev@email.com> (24-03-2021)",
        ):
            self.masks_path.write_text(
                textwrap.dedent(
                    f"""\
                        # Random Dev <random.dev@email.com> (2021-03-24)
                        # masked
                        cat/masked

                        {line}
                        # masked
                        cat/masked2
                    """
                )
            )

            with (
                os_environ("VISUAL", EDITOR="sed -i '1s/$/mask comment/'"),
                patch("sys.argv", self.args + ["=cat/pkg-0"]),
                pytest.raises(SystemExit),
                chdir(pjoin(self.repo.path)),
            ):
                self.script()
            _, err = capsys.readouterr()
            assert "pkgdev mask: error: invalid author, lineno 5" in err

    def test_last_rites(self):
        for rflag in ("-r", "--rites"):
            for args in ([rflag], [rflag, "14"]):
                with (
                    os_environ("VISUAL", EDITOR="sed -i '1s/$/mask comment/'"),
                    patch("sys.argv", self.args + ["cat/pkg"] + args),
                    pytest.raises(SystemExit),
                    chdir(pjoin(self.repo.path)),
                ):
                    self.script()

                days = 30 if len(args) == 1 else int(args[1])
                removal_date = self.today + timedelta(days=days)
                today = self.today.strftime("%Y-%m-%d")
                removal = removal_date.strftime("%Y-%m-%d")
                assert self.masks_path.read_text() == textwrap.dedent(
                    f"""\
                        # First Last <first.last@email.com> ({today})
                        # mask comment
                        # Removal: {removal}.
                        cat/pkg
                    """
                )
                self.masks_path.write_text("")  # Reset the contents of package.mask

    @pytest.mark.skipif(sys.platform == "darwin", reason="no xdg-email on mac os")
    def test_last_rites_with_email(self, tmp_path):
        output_file = tmp_path / "mail.txt"
        for rflag in ("-r", "--rites"):
            with (
                os_environ(
                    "VISUAL", EDITOR="sed -i '1s/$/mask comment/'", MAILER=f"> {output_file} echo"
                ),
                patch("sys.argv", self.args + ["cat/pkg", rflag, "--email"]),
                pytest.raises(SystemExit),
                chdir(pjoin(self.repo.path)),
            ):
                self.script()
            out = output_file.read_text()
            assert "mailto:gentoo-dev-announce@lists.gentoo.org" in out

            self.masks_path.write_text("")  # Reset the contents of package.mask

    @pytest.mark.skipif(sys.platform == "darwin", reason="no xdg-email on mac os")
    def test_last_email_bad_mailer(self, capsys):
        for rflag in ("-r", "--rites"):
            with (
                os_environ("VISUAL", EDITOR="sed -i '1s/$/mask comment/'", MAILER="false"),
                patch("sys.argv", self.args + ["cat/pkg", rflag, "--email"]),
                pytest.raises(SystemExit),
                chdir(pjoin(self.repo.path)),
            ):
                self.script()
            _, err = capsys.readouterr()
            assert err.strip() == "pkgdev mask: error: failed opening email composer"

    def test_mask_bugs(self):
        today = self.today.strftime("%Y-%m-%d")
        for bflag in ("-b", "--bug"):
            for bug_nums, expected in [
                (["42"], "Bug #42."),
                (["42", "43"], "Bugs #42, #43."),
            ]:
                args = []
                for bug_num in bug_nums:
                    args += [bflag, bug_num]
                with (
                    os_environ("VISUAL", EDITOR="sed -i '1s/$/mask comment/'"),
                    patch("sys.argv", self.args + ["cat/pkg"] + args),
                    pytest.raises(SystemExit),
                    chdir(pjoin(self.repo.path)),
                ):
                    self.script()

                assert self.masks_path.read_text() == textwrap.dedent(
                    f"""\
                        # First Last <first.last@email.com> ({today})
                        # mask comment
                        # {expected}
                        cat/pkg
                    """
                )
                self.masks_path.write_text("")  # Reset the contents of package.mask

    def test_mask_bug_bad(self, capsys, tool):
        for arg, expected in [("-1", "must be >= 1"), ("foo", "invalid integer value")]:
            with pytest.raises(SystemExit):
                tool.parse_args(["mask", "--bug", arg])
            out, err = capsys.readouterr()
            assert err.strip() == f"pkgdev mask: error: argument -b/--bug: {expected}"
