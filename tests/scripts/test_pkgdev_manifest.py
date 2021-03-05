from functools import partial
from unittest.mock import patch

import pytest
from pkgcore.ebuild.atom import atom as atom_cls
from pkgdev.scripts import run
from snakeoil.contexts import chdir


class TestPkgdevManifestParseArgs:

    def test_non_repo_cwd(self, capsys, tool):
        with pytest.raises(SystemExit) as excinfo:
            tool.parse_args(['manifest'])
        assert excinfo.value.code == 2
        out, err = capsys.readouterr()
        err = err.strip().split('\n')[-1]
        assert err.endswith('error: not in ebuild repo')

    def test_repo_cwd(self, repo, capsys, tool):
        with chdir(repo.location):
            options, _ = tool.parse_args(['manifest'])
        assert options.restrictions

    def test_atom_target(self, repo, capsys, tool):
        with chdir(repo.location):
            options, _ = tool.parse_args(['manifest', 'cat/pkg'])
        assert options.restrictions == [atom_cls('cat/pkg')]

    def test_invalid_atom_target(self, repo, capsys, tool):
        with pytest.raises(SystemExit) as excinfo, \
                chdir(repo.location):
            tool.parse_args(['manifest', '=cat/pkg'])
        assert excinfo.value.code == 2
        out, err = capsys.readouterr()
        err == "pkgdev manifest: error: invalid atom: '=cat/pkg'"


class TestPkgdevManifest:

    script = partial(run, 'pkgdev')

    @pytest.fixture(autouse=True)
    def _setup(self):
        self.args = ['pkgdev', 'manifest']

    def test_good_manifest(self, capsys, repo):
        repo.create_ebuild('cat/pkg-0')
        with patch('sys.argv', self.args), \
                pytest.raises(SystemExit) as excinfo, \
                chdir(repo.location):
            self.script()
        assert excinfo.value.code == 0
        out, err = capsys.readouterr()
        assert out == err == ''

    def test_bad_manifest(self, capsys, repo):
        repo.create_ebuild('cat/pkg-0')
        repo.create_ebuild('cat/pkg-1', eapi='-1')
        with patch('sys.argv', self.args), \
                pytest.raises(SystemExit) as excinfo, \
                chdir(repo.location):
            self.script()
        assert excinfo.value.code == 1
        out, err = capsys.readouterr()
        assert not err
        assert out == " * cat/pkg-1: invalid EAPI '-1'\n"
