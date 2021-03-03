import subprocess

from snakeoil.cli.exceptions import UserException


def run(*args, **kwargs):
    """Wrapper for running git via subprocess.run()."""
    kwargs.setdefault('check', True)
    kwargs.setdefault('text', True)
    cmd = ['git'] + list(args)
    try:
        return subprocess.run(cmd, **kwargs)
    except FileNotFoundError as e:
        raise UserException(str(e))
    except subprocess.CalledProcessError as e:
        raise SystemExit(e.returncode)
