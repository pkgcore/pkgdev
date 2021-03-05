import os

import pytest
from snakeoil.contexts import chdir
from snakeoil.osutils import pjoin


class TestPkgdevPushParseArgs:

    def test_non_repo_cwd(self, capsys, tool):
        with pytest.raises(SystemExit):
            tool.parse_args(['commit'])
        out, err = capsys.readouterr()
        err = err.strip().split('\n')[-1]
        assert err.endswith('error: not in ebuild repo')

    def test_non_git_repo_cwd(self, repo, capsys, tool):
        with pytest.raises(SystemExit), \
                chdir(repo.location):
            tool.parse_args(['commit'])
        out, err = capsys.readouterr()
        err = err.strip().split('\n')[-1]
        assert err.endswith('error: not in git repo')

    def test_non_ebuild_git_repo_cwd(self, make_repo, git_repo, capsys, tool):
        os.mkdir(pjoin(git_repo.path, 'repo'))
        repo = make_repo(pjoin(git_repo.path, 'repo'))
        with pytest.raises(SystemExit), \
                chdir(repo.location):
            tool.parse_args(['commit'])
        out, err = capsys.readouterr()
        err = err.strip().split('\n')[-1]
        assert err.endswith('error: not in ebuild git repo')

    def test_git_push_args_passthrough(self, repo, make_git_repo, tool):
        """Unknown arguments for ``pkgdev push`` are passed to ``git push``."""
        git_repo = make_git_repo(repo.location)
        with chdir(git_repo.path):
            options, _ = tool.parse_args(['push', 'origin', 'master'])
        assert options.push_args == ['origin', 'master']
