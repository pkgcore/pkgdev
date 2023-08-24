import argparse
import shlex

from pkgcheck import reporters, scan
from pkgcheck.results import Warning as PkgcheckWarning
from snakeoil.cli import arghparse
from snakeoil.cli.input import userquery

from .. import cli, git
from .argparsers import cwd_repo_argparser, git_repo_argparser


class ArgumentParser(cli.ArgumentParser):
    """Parse all known arguments, passing unknown arguments to ``git push``."""

    def parse_known_args(self, args=None, namespace=None):
        namespace, args = super().parse_known_args(args, namespace)
        if namespace.dry_run:
            args.append("--dry-run")
        namespace.push_args = args
        return namespace, []


push = ArgumentParser(
    prog="pkgdev push",
    description="run QA checks on commits and push them",
    parents=(cwd_repo_argparser, git_repo_argparser),
)
# custom `pkgcheck scan` args used for tests
push.add_argument("--pkgcheck-scan", help=argparse.SUPPRESS)
push_opts = push.add_argument_group("push options")
push_opts.add_argument(
    "-A",
    "--ask",
    nargs="?",
    const=True,
    action=arghparse.StoreBool,
    help="confirm pushing commits with QA errors",
)
push_opts.add_argument("-n", "--dry-run", action="store_true", help="pretend to push the commits")
push_opts.add_argument(
    "--pull", action="store_true", help="run `git pull --rebase` before scanning"
)


@push.bind_final_check
def _commit_validate(parser, namespace):
    # determine `pkgcheck scan` args
    namespace.scan_args = ["-v"] * namespace.verbosity
    if namespace.pkgcheck_scan:
        namespace.scan_args.extend(shlex.split(namespace.pkgcheck_scan))
    namespace.scan_args.extend(["--exit", "GentooCI", "--commits"])


@push.bind_main_func
def _push(options, out, err):
    if options.pull:
        git.run("pull", "--rebase", cwd=options.repo.location)

    # scan commits for QA issues
    pipe = scan(options.scan_args)
    has_warnings = False
    with reporters.FancyReporter(out) as reporter:
        for result in pipe:
            reporter.report(result)
            if result.level == PkgcheckWarning.level:
                has_warnings = True

    # fail on errors unless they're ignored
    if pipe.errors:
        with reporters.FancyReporter(out) as reporter:
            out.write(out.bold, out.fg("red"), "\nFAILURES", out.reset)
            for result in sorted(pipe.errors):
                reporter.report(result)
        if not (options.ask and userquery("Push commits anyway?", out, err, default_answer=False)):
            return 1
    elif has_warnings and options.ask:
        if not userquery("warnings detected, push commits anyway?", out, err, default_answer=False):
            return 1

    # push commits upstream
    git.run("push", *options.push_args, cwd=options.repo.location)

    return 0
