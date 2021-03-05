from functools import partial
from unittest.mock import patch

import pytest
from pkgdev.scripts import run
from snakeoil.contexts import chdir
from snakeoil.osutils import pjoin


class TestPkgdevManifestParseArgs:

    def test_non_repo_cwd(self, capsys, tool):
        with pytest.raises(SystemExit) as excinfo:
            tool.parse_args(['manifest'])
        assert excinfo.value.code == 2
        out, err = capsys.readouterr()
        assert err.strip() == 'pkgdev manifest: error: not in ebuild repo'

    def test_repo_cwd(self, repo, capsys, tool):
        repo.create_ebuild('cat/pkg-0')
        with chdir(repo.location):
            options, _ = tool.parse_args(['manifest'])
        matches = [x.cpvstr for x in repo.itermatch(options.restriction)]
        assert matches == ['cat/pkg-0']

    def test_dir_target(self, repo, capsys, tool):
        repo.create_ebuild('cat/pkg-0')
        with chdir(repo.location):
            options, _ = tool.parse_args(['manifest', pjoin(repo.location, 'cat')])
        matches = [x.cpvstr for x in repo.itermatch(options.restriction)]
        assert matches == ['cat/pkg-0']

    def test_ebuild_target(self, repo, capsys, tool):
        path = repo.create_ebuild('cat/pkg-0')
        with chdir(repo.location):
            options, _ = tool.parse_args(['manifest', path])
        matches = [x.cpvstr for x in repo.itermatch(options.restriction)]
        assert matches == ['cat/pkg-0']

    def test_atom_target(self, repo, capsys, tool):
        repo.create_ebuild('cat/pkg-0')
        with chdir(repo.location):
            options, _ = tool.parse_args(['manifest', 'cat/pkg'])
        matches = [x.cpvstr for x in repo.itermatch(options.restriction)]
        assert matches == ['cat/pkg-0']

    def test_non_repo_dir_target(self, tmp_path, repo, capsys, tool):
        with pytest.raises(SystemExit) as excinfo, \
                chdir(repo.location):
            tool.parse_args(['manifest', str(tmp_path)])
        assert excinfo.value.code == 2
        out, err = capsys.readouterr()
        assert err.startswith("pkgdev manifest: error: 'fake' repo doesn't contain:")

    def test_invalid_atom_target(self, repo, capsys, tool):
        with pytest.raises(SystemExit) as excinfo, \
                chdir(repo.location):
            tool.parse_args(['manifest', '=cat/pkg'])
        assert excinfo.value.code == 2
        out, err = capsys.readouterr()
        assert err.startswith("pkgdev manifest: error: invalid atom: '=cat/pkg'")


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
