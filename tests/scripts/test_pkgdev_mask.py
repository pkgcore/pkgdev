import os
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


class TestPkgdevMask:

    script = partial(run, 'pkgdev')

    @pytest.fixture(autouse=True)
    def _setup(self, make_repo, make_git_repo):
        # args for running pkgdev like a script
        self.args = ['pkgdev', 'mask']
        self.repo = make_repo(arches=['amd64'])
        self.git_repo = make_git_repo(self.repo.location)

        # create profile
        self.profile_path = pjoin(self.repo.location, 'profiles/arch/amd64')
        os.makedirs(self.profile_path)
        with open(pjoin(self.repo.location, 'profiles/profiles.desc'), 'w') as f:
            f.write('amd64 arch/amd64 stable\n')

        profile = list(self.repo.config.profiles)[0]
        self.profile = self.repo.config.profiles.create_profile(profile)
        self.masks_path = Path(pjoin(self.repo.location, 'profiles/package.mask'))

    def test_empty_repo(self):
        assert self.profile.masks == frozenset()

    def test_mask_cwd(self):
        self.repo.create_ebuild('cat/pkg-0')
        self.git_repo.add_all('cat/pkg-0')
        with os_environ(EDITOR="sed -i '1s/$/mask comment/'"), \
                patch('sys.argv', self.args), \
                pytest.raises(SystemExit) as excinfo, \
                chdir(pjoin(self.repo.path, 'cat/pkg')):
            self.script()
        assert excinfo.value.code == 0
        assert self.profile.masks == frozenset([atom_cls('cat/pkg')])
