from functools import partial
from unittest.mock import patch

import pytest
from pkgdev.scripts import run
from snakeoil.contexts import chdir


class TestPkgdevManifestParseArgs:

    def test_non_repo_cwd(self, capsys, tool):
        with pytest.raises(SystemExit):
            tool.parse_args(['manifest'])
        out, err = capsys.readouterr()
        err = err.strip().split('\n')[-1]
        assert err.endswith('error: not in ebuild repo')


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
