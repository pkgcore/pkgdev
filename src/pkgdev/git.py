import subprocess

from snakeoil.cli.exceptions import UserException


def run(*args, **kwargs):
    """Wrapper for running git via subprocess.run()."""
    kwargs.setdefault('check', True)
    kwargs.setdefault('stderr', subprocess.PIPE)
    kwargs.setdefault('text', True)
    try:
        return subprocess.run(['git'] + list(*args), **kwargs)
    except FileNotFoundError as e:
        raise UserException(str(e))
    except subprocess.CalledProcessError as e:
        error = e.stderr.splitlines()[0]
        raise UserException(error)
