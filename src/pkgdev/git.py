import os
import subprocess
import sys

from snakeoil.cli.exceptions import UserException


class GitError(SystemExit):
    """Generic error running a git command."""


def run(*args, **kwargs):
    """Wrapper for running git via subprocess.run()."""
    kwargs.setdefault("check", True)
    kwargs.setdefault("text", True)
    kwargs.setdefault("env", os.environ.copy())["PKGDEV"] = "1"
    cmd = ["git"] + list(args)

    # output git command that would be run to stderr
    if "--dry-run" in args:
        git_cmd = " ".join(x for x in cmd if x != "--dry-run")
        sys.stderr.write(f"{git_cmd}\n")

    try:
        return subprocess.run(cmd, **kwargs)
    except FileNotFoundError as exc:
        raise UserException(str(exc))
    except subprocess.CalledProcessError as exc:
        raise GitError(exc.returncode)
