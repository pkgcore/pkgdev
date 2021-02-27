import os

from pkgcore.repository import errors as repo_errors
from snakeoil.cli import arghparse
from snakeoil.cli.exceptions import UserException
from snakeoil.process import CommandNotFound, find_binary


git_argparser = arghparse.ArgumentParser(suppress=True)
cwd_repo_argparser = arghparse.ArgumentParser(suppress=True)


@git_argparser.bind_delayed_default(0, 'git')
def _determine_git(namespace, attr):
    # verify git exists on the system
    try:
        setattr(namespace, attr, find_binary('git'))
    except CommandNotFound:
        raise UserException('git not found')


@cwd_repo_argparser.bind_delayed_default(0, 'repo')
def _determine_cwd_repo(namespace, attr):
    namespace.cwd = os.getcwd()
    try:
        repo = namespace.domain.find_repo(
            namespace.cwd, config=namespace.config, configure=False)
    except (repo_errors.InitializationError, IOError) as e:
        raise UserException(str(e))

    if repo is None:
        raise UserException('current working directory not in ebuild repo')

    setattr(namespace, attr, repo)
