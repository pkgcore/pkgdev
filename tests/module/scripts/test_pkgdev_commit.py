from functools import partial
from unittest.mock import patch

import pytest
from pkgdev.scripts import run
from snakeoil.contexts import chdir, os_environ


class TestPkgCommit:

    script = partial(run, 'pkgdev')

    @pytest.fixture(autouse=True)
    def _setup(self, tmp_path):
        self.cache_dir = str(tmp_path)
        self.scan_args = ['--scan-args', f'--config no --cache-dir {self.cache_dir}']
        # args for running pkgdev like a script
        self.args = ['pkgdev', 'commit'] + self.scan_args

    def test_no_staged_changes(self, capsys, repo, make_git_repo):
        git_repo = make_git_repo(repo.location)
        repo.create_ebuild('cat/pkg-0')
        git_repo.add_all('cat/pkg-0')

        for opt in ([], ['-u'], ['-a']):
            with patch('sys.argv', self.args + opt), \
                    pytest.raises(SystemExit) as excinfo, \
                    chdir(git_repo.path):
                self.script()
            assert excinfo.value.code == 2
            out, err = capsys.readouterr()
            assert not out
            assert err.strip() == 'pkgdev commit: error: no staged changes exist'

    def test_stage_changed_files(self, capsys, repo, make_git_repo, editor):
        git_repo = make_git_repo(repo.location)
        ebuild_path = repo.create_ebuild('cat/pkg-0')
        git_repo.add_all('cat/pkg-0')
        with open(ebuild_path, 'a+') as f:
            f.write('# comment\n')

        with os_environ(GIT_EDITOR="sed -i '1s/$/commit/'"), \
                patch('sys.argv', self.args + ['-u']), \
                pytest.raises(SystemExit) as excinfo, \
                chdir(git_repo.path):
            self.script()
        assert excinfo.value.code == 0
        out, err = capsys.readouterr()
        assert err == out == ''

        commit_msg = git_repo.log(['-1', '--pretty=tformat:%B', 'HEAD'])
        assert commit_msg == ['cat/pkg: commit']
