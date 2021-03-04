import os
import subprocess

from pkgcore.repository import errors as repo_errors
from snakeoil.cli.arghparse import ArgumentParser
from snakeoil.cli.exceptions import UserException

from .. import git

cwd_repo_argparser = ArgumentParser(suppress=True)
git_repo_argparser = ArgumentParser(suppress=True)


@cwd_repo_argparser.bind_delayed_default(0, 'repo')
def _determine_cwd_repo(namespace, attr):
    namespace.cwd = os.getcwd()
    try:
        repo = namespace.domain.find_repo(
            namespace.cwd, config=namespace.config, configure=False)
    except (repo_errors.InitializationError, IOError) as e:
        raise UserException(str(e))

    if repo is None:
        raise UserException('not in ebuild repo')

    setattr(namespace, attr, repo)


@git_repo_argparser.bind_delayed_default(1, 'git_repo')
def _determine_git_repo(namespace, attr):
    try:
        p = git.run('rev-parse', '--show-toplevel', stdout=subprocess.PIPE)
        path = p.stdout.strip()
    except SystemExit:
        raise UserException('not in git repo')

    # verify the git and ebuild repo roots match when using both
    try:
        if namespace.repo.location != path:
            raise UserException('not in ebuild git repo')
    except AttributeError:
        # ebuild repo parser not enabled
        pass

    setattr(namespace, attr, path)
