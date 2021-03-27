import os
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
            tool.parse_args(['mask'])
        out, err = capsys.readouterr()
        assert err.strip() == 'pkgdev mask: error: not in ebuild repo'

    def test_non_git_repo_cwd(self, repo, capsys, tool):
        with pytest.raises(SystemExit), \
                chdir(repo.location):
            tool.parse_args(['mask'])
        out, err = capsys.readouterr()
        assert err.strip() == 'pkgdev mask: error: not in git repo'

    def test_non_ebuild_git_repo_cwd(self, make_repo, git_repo, capsys, tool):
        os.mkdir(pjoin(git_repo.path, 'repo'))
        repo = make_repo(pjoin(git_repo.path, 'repo'))
        with pytest.raises(SystemExit), \
                chdir(repo.location):
            tool.parse_args(['mask'])
        out, err = capsys.readouterr()
        assert err.strip() == 'pkgdev mask: error: not in ebuild git repo'

    def test_cwd_target(self, repo, make_git_repo, capsys, tool):
        git_repo = make_git_repo(repo.location)
        # empty repo
        with pytest.raises(SystemExit), \
                chdir(repo.location):
            tool.parse_args(['mask'])
        out, err = capsys.readouterr()
        assert err.strip() == 'pkgdev mask: error: not in a package directory'

        # not in package dir
        repo.create_ebuild('cat/pkg-0')
        git_repo.add_all('cat/pkg-0')
        with pytest.raises(SystemExit), \
                chdir(repo.location):
            tool.parse_args(['mask'])
        out, err = capsys.readouterr()
        assert err.strip() == 'pkgdev mask: error: not in a package directory'

        # masking CWD package
        with chdir(pjoin(repo.location, 'cat/pkg')):
            options, _ = tool.parse_args(['mask'])
        assert options.atoms == [atom_cls('cat/pkg')]

    def test_targets(self, repo, make_git_repo, capsys, tool):
        git_repo = make_git_repo(repo.location)

        # invalid atom
        with pytest.raises(SystemExit), \
                chdir(repo.location):
            tool.parse_args(['mask', 'pkg'])
        out, err = capsys.readouterr()
        assert err.strip() == "pkgdev mask: error: invalid atom: 'pkg'"

        # nonexistent pkg
        with pytest.raises(SystemExit), \
                chdir(repo.location):
            tool.parse_args(['mask', 'cat/nonexistent'])
        out, err = capsys.readouterr()
        assert err.strip() == "pkgdev mask: error: no repo matches: 'cat/nonexistent'"

        # masked pkg
        repo.create_ebuild('cat/pkg-0')
        git_repo.add_all('cat/pkg-0')
        with chdir(repo.location):
            options, _ = tool.parse_args(['mask', 'cat/pkg'])
        assert options.atoms == [atom_cls('cat/pkg')]


class TestPkgdevMask:

    script = partial(run, 'pkgdev')

    @pytest.fixture(autouse=True)
    def _setup(self, make_repo, make_git_repo):
        # args for running pkgdev like a script
        self.args = ['pkgdev', 'mask']
        self.repo = make_repo(arches=['amd64'])
        self.git_repo = make_git_repo(self.repo.location)
        self.today = datetime.now(timezone.utc)

        # add stub pkg
        self.repo.create_ebuild('cat/pkg-0')
        self.git_repo.add_all('cat/pkg-0')

        # create profile
        self.profile_path = pjoin(self.repo.location, 'profiles/arch/amd64')
        os.makedirs(self.profile_path)
        with open(pjoin(self.repo.location, 'profiles/profiles.desc'), 'w') as f:
            f.write('amd64 arch/amd64 stable\n')

        self.masks_path = Path(pjoin(self.repo.location, 'profiles/package.mask'))

    @property
    def profile(self):
        profile = list(self.repo.config.profiles)[0]
        return self.repo.config.profiles.create_profile(profile)

    def test_empty_repo(self):
        assert self.profile.masks == frozenset()

    def test_nonexistent_editor(self, capsys):
        with os_environ(EDITOR='12345'), \
                patch('sys.argv', self.args + ['cat/pkg']), \
                pytest.raises(SystemExit), \
                chdir(pjoin(self.repo.path)):
            self.script()
        out, err = capsys.readouterr()
        assert err.strip() == "pkgdev mask: error: nonexistent editor: '12345'"

    def test_failed_editor(self, capsys):
        with os_environ(EDITOR="sed -i 's///'"), \
                patch('sys.argv', self.args + ['cat/pkg']), \
                pytest.raises(SystemExit), \
                chdir(pjoin(self.repo.path)):
            self.script()
        out, err = capsys.readouterr()
        assert err.strip() == "pkgdev mask: error: failed writing mask comment"

    def test_empty_mask_comment(self, capsys):
        with os_environ(EDITOR="sed -i 's/#/#/'"), \
                patch('sys.argv', self.args + ['cat/pkg']), \
                pytest.raises(SystemExit), \
                chdir(pjoin(self.repo.path)):
            self.script()
        out, err = capsys.readouterr()
        assert err.strip() == "pkgdev mask: error: empty mask comment"

    def test_mask_cwd(self):
        with os_environ(EDITOR="sed -i '1s/$/mask comment/'"), \
                patch('sys.argv', self.args), \
                pytest.raises(SystemExit), \
                chdir(pjoin(self.repo.path, 'cat/pkg')):
            self.script()
        assert self.profile.masks == frozenset([atom_cls('cat/pkg')])

    def test_mask_target(self):
        with os_environ(EDITOR="sed -i '1s/$/mask comment/'"), \
                patch('sys.argv', self.args + ['cat/pkg']), \
                pytest.raises(SystemExit), \
                chdir(pjoin(self.repo.path)):
            self.script()
        assert self.profile.masks == frozenset([atom_cls('cat/pkg')])

    def test_mask_ebuild_path(self):
        with os_environ(EDITOR="sed -i '1s/$/mask comment/'"), \
                patch('sys.argv', self.args + ['cat/pkg/pkg-0.ebuild']), \
                pytest.raises(SystemExit), \
                chdir(pjoin(self.repo.path)):
            self.script()
        assert self.profile.masks == frozenset([atom_cls('=cat/pkg-0')])

    def test_existing_masks(self):
        self.masks_path.write_text(textwrap.dedent("""\
            # Random Dev <random.dev@email.com> (2021-03-24)
            # masked
            cat/masked
        """))

        with os_environ(EDITOR="sed -i '1s/$/mask comment/'"), \
                patch('sys.argv', self.args + ['=cat/pkg-0']), \
                pytest.raises(SystemExit), \
                chdir(pjoin(self.repo.path)):
            self.script()
        assert self.profile.masks == frozenset([atom_cls('cat/masked'), atom_cls('=cat/pkg-0')])

    def test_last_rites(self):
        with os_environ(EDITOR="sed -i '1s/$/mask comment/'"), \
                patch('sys.argv', self.args + ['cat/pkg', '-r']), \
                pytest.raises(SystemExit), \
                chdir(pjoin(self.repo.path)):
            self.script()

        removal_date = self.today + timedelta(days=30)
        today = self.today.strftime('%Y-%m-%d')
        removal = removal_date.strftime('%Y-%m-%d')
        assert self.masks_path.read_text() == textwrap.dedent(f"""\
            # First Last <first.last@email.com> ({today})
            # mask comment
            # Removal: {removal}
            cat/pkg
        """)
