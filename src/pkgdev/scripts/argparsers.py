import os
import subprocess

from pkgcore.repository import errors as repo_errors
from snakeoil.cli.arghparse import ArgumentParser
from snakeoil.cli.exceptions import UserException


class GitArgumentParser(ArgumentParser):
    """Argument parser with extra git functionality."""

    def run_git(self, *args, **kwargs):
        """Wrapper for running git via subprocess.run()."""
        kwargs.setdefault('check', True)
        kwargs.setdefault('stderr', subprocess.PIPE)
        kwargs.setdefault('text', True)
        try:
            return subprocess.run(['git'] + list(*args), **kwargs)
        except FileNotFoundError:
            raise UserException('git not found')
        except subprocess.CalledProcessError as e:
            error = e.stderr.splitlines()[0]
            raise UserException(error)


cwd_repo_argparser = ArgumentParser(suppress=True)


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
