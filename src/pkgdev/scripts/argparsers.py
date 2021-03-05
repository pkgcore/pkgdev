import os
import subprocess

from pkgcore.repository import errors as repo_errors
from snakeoil.cli.arghparse import ArgumentParser
from snakeoil.cli.exceptions import UserException

from .. import git

cwd_repo_argparser = ArgumentParser(suppress=True)
git_repo_argparser = ArgumentParser(suppress=True)


@cwd_repo_argparser.bind_early_parse
def _determine_cwd_repo(parser, namespace, args):
    namespace.cwd = os.getcwd()
    try:
        repo = namespace.domain.find_repo(
            namespace.cwd, config=namespace.config, configure=False)
    except (repo_errors.InitializationError, IOError) as e:
        raise UserException(str(e))

    if repo is None:
        raise UserException('not in ebuild repo')

    namespace.repo = repo
    return namespace, args


@git_repo_argparser.bind_early_parse
def _determine_git_repo(parser, namespace, args):
    try:
        p = git.run('rev-parse', '--show-toplevel', stdout=subprocess.PIPE)
        path = p.stdout.strip()
    except git.GitError:
        raise UserException('not in git repo')

    # verify the git and ebuild repo roots match when using both
    try:
        if namespace.repo.location != path:
            raise UserException('not in ebuild git repo')
    except AttributeError:
        # ebuild repo parser not enabled
        pass

    namespace.git_repo = path
    return namespace, args
