import os
import subprocess
from configparser import ConfigParser
from contextlib import suppress
from pathlib import Path

from pkgcore.repository import errors as repo_errors
from snakeoil.cli.arghparse import ArgumentParser

from .. import git

cwd_repo_argparser = ArgumentParser(suppress=True)
git_repo_argparser = ArgumentParser(suppress=True)


@cwd_repo_argparser.bind_final_check
def _determine_cwd_repo(parser, namespace):
    namespace.cwd = os.getcwd()
    try:
        repo = namespace.domain.find_repo(namespace.cwd, config=namespace.config, configure=False)
    except (repo_errors.InitializationError, IOError) as e:
        raise parser.error(str(e))

    if repo is None:
        raise parser.error("not in ebuild repo")

    namespace.repo = repo


@git_repo_argparser.bind_final_check
def _determine_git_repo(parser, namespace):
    try:
        p = git.run("rev-parse", "--show-toplevel", stdout=subprocess.PIPE)
        path = p.stdout.strip()
    except git.GitError:
        raise parser.error("not in git repo")

    # verify the git and ebuild repo roots match when using both
    try:
        if namespace.repo.location != path:
            raise parser.error("not in ebuild git repo")
    except AttributeError:
        # ebuild repo parser not enabled
        pass

    namespace.git_repo = path


class BugzillaApiKey:
    @classmethod
    def mangle_argparser(cls, parser):
        parser.add_argument(
            "--api-key",
            metavar="TOKEN",
            help="Bugzilla API key",
            docs="""
                The Bugzilla API key to use for authentication. WARNING: using this
                option will expose your API key to other users of the same system.
                Consider instead saving your API key in a file named ``~/.bugzrc``
                in an INI format like so::

                        [default]
                        key = <your API key>

                Another supported option is to save your API key in a file named
                ``~/.bugz_token``.
            """,
        )

        parser.bind_delayed_default(1000, "api_key")(cls._default_api_key)

    @staticmethod
    def _default_api_key(namespace, attr):
        """Use all known arches by default."""
        if (bugz_rc_file := Path.home() / ".bugzrc").is_file():
            try:
                config = ConfigParser(default_section="default")
                config.read(bugz_rc_file)
            except Exception as e:
                raise ValueError(f"failed parsing {bugz_rc_file}: {e}")

            for category in ("default", "gentoo", "Gentoo"):
                with suppress(Exception):
                    setattr(namespace, attr, config.get(category, "key"))
                    return

        if (bugz_token_file := Path.home() / ".bugz_token").is_file():
            setattr(namespace, attr, bugz_token_file.read_text().strip())
