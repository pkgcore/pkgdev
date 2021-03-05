import os
import textwrap
from functools import partial
from unittest.mock import patch

import pytest
from pkgdev.scripts import run
from snakeoil.contexts import chdir
from snakeoil.osutils import pjoin


class TestPkgdevPushParseArgs:

    def test_non_repo_cwd(self, capsys, tool):
        with pytest.raises(SystemExit):
            tool.parse_args(['push'])
        out, err = capsys.readouterr()
        assert err.strip() == 'pkgdev push: error: not in ebuild repo'

    def test_non_git_repo_cwd(self, repo, capsys, tool):
        with pytest.raises(SystemExit), \
                chdir(repo.location):
            tool.parse_args(['push'])
        out, err = capsys.readouterr()
        assert err.strip() == 'pkgdev push: error: not in git repo'

    def test_non_ebuild_git_repo_cwd(self, make_repo, git_repo, capsys, tool):
        os.mkdir(pjoin(git_repo.path, 'repo'))
        repo = make_repo(pjoin(git_repo.path, 'repo'))
        with pytest.raises(SystemExit), \
                chdir(repo.location):
            tool.parse_args(['push'])
        out, err = capsys.readouterr()
        assert err.strip() == 'pkgdev push: error: not in ebuild git repo'

    def test_git_push_args_passthrough(self, repo, make_git_repo, tool):
        """Unknown arguments for ``pkgdev push`` are passed to ``git push``."""
        git_repo = make_git_repo(repo.location)
        with chdir(git_repo.path):
            options, _ = tool.parse_args(['push', 'origin', 'master'])
        assert options.push_args == ['origin', 'master']

    def test_gentoo_repo_git_push_args(self, make_repo, make_git_repo, tool):
        """Unknown arguments for ``pkgdev push`` are passed to ``git push``."""
        repo = make_repo(repo_id='gentoo')
        git_repo = make_git_repo(repo.location)
        with chdir(git_repo.path):
            options, _ = tool.parse_args(['push', '-n'])
        assert '--signed' in options.push_args
        assert '--dry-run' in options.push_args


class TestPkgdevPush:

    script = partial(run, 'pkgdev')

    @pytest.fixture(autouse=True)
    def _setup(self, tmp_path, make_repo, make_git_repo):
        self.cache_dir = str(tmp_path / 'cache')
        self.scan_args = ['--pkgcheck-scan', f'--config no --cache-dir {self.cache_dir}']
        # args for running pkgdev like a script
        self.args = ['pkgdev', 'push'] + self.scan_args

        # initialize parent repo
        self.parent_git_repo = make_git_repo(bare=True)
        # initialize child repo
        child_repo_path = tmp_path / 'child-repo'
        child_repo_path.mkdir()
        self.child_git_repo = make_git_repo(str(child_repo_path))
        self.child_repo = make_repo(self.child_git_repo.path)
        self.child_git_repo.add_all('initial commit')
        # create a stub pkg and commit it
        self.child_repo.create_ebuild('cat/pkg-0')
        self.child_git_repo.add_all('cat/pkg-0')
        # set up parent repo as origin and push to it
        self.child_git_repo.run(['git', 'remote', 'add', 'origin', self.parent_git_repo.path])
        self.child_git_repo.run(['git', 'push', '-u', 'origin', 'master'])
        self.child_git_repo.run(['git', 'remote', 'set-head', 'origin', 'master'])

    def test_push(self, capsys):
        self.child_repo.create_ebuild('cat/pkg-1')
        self.child_git_repo.add_all('cat/pkg-1')

        with patch('sys.argv', self.args), \
                pytest.raises(SystemExit) as excinfo, \
                chdir(self.child_git_repo.path):
            self.script()
        assert excinfo.value.code == 0

    def test_failed_push(self, capsys):
        self.child_repo.create_ebuild('cat/pkg-1', eapi='-1')
        self.child_git_repo.add_all('cat/pkg-1')

        # failed scans don't push commits
        with patch('sys.argv', self.args), \
                pytest.raises(SystemExit) as excinfo, \
                chdir(self.child_git_repo.path):
            self.script()
        assert excinfo.value.code == 1
        out, err = capsys.readouterr()
        assert out == textwrap.dedent("""\
            cat/pkg
              InvalidEapi: version 1: invalid EAPI '-1'

            FAILURES
            cat/pkg
              InvalidEapi: version 1: invalid EAPI '-1'
        """)

        # but failures can be ignored to push anyway
        with patch('sys.argv', self.args + ['--ignore-failures']), \
                pytest.raises(SystemExit) as excinfo, \
                chdir(self.child_git_repo.path):
            self.script()
        assert excinfo.value.code == 0
