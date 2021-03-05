import argparse
import shlex

from pkgcheck import reporters, scan
from snakeoil.cli import arghparse

from .. import git
from .argparsers import cwd_repo_argparser, git_repo_argparser


class ArgumentParser(arghparse.ArgumentParser):
    """Parse all known arguments, passing unknown arguments to ``git push``."""

    def parse_known_args(self, args=None, namespace=None):
        namespace, args = super().parse_known_args(args, namespace)
        namespace.extended_push_args = args
        return namespace, []


push = ArgumentParser(
    prog='pkgdev push', description='run QA checks on commits and push them',
    parents=(cwd_repo_argparser, git_repo_argparser))
# custom `pkgcheck scan` args used for tests
push.add_argument('--pkgcheck-scan', help=argparse.SUPPRESS)
push.add_argument(
    '--ignore-failures', action='store_true',
    help='ignore QA failures before pushing')
push.add_argument(
    '-n', '--dry-run', action='store_true',
    help='pretend to push the commits')


def determine_push_args(namespace):
    """Determine arguments used with `git push`."""
    args = []
    if namespace.repo.repo_id == 'gentoo':
        # gentoo repo requires signed pushes
        args.append('--signed')
    if namespace.dry_run:
        args.append('--dry-run')
    return args


@push.bind_final_check
def _commit_validate(parser, namespace):
    # determine `git push` args
    namespace.push_args = determine_push_args(namespace) + namespace.extended_push_args

    # determine `pkgcheck scan` args
    namespace.scan_args = []
    if namespace.pkgcheck_scan:
        namespace.scan_args.extend(shlex.split(namespace.pkgcheck_scan))
    namespace.scan_args.extend(['--exit', 'GentooCI', '--commits'])


@push.bind_main_func
def _push(options, out, err):
    # scan commits for QA issues
    pipe = scan(options.scan_args)
    with reporters.FancyReporter(out) as reporter:
        for result in pipe:
            reporter.report(result)

    # fail on errors unless they're ignored
    if pipe.errors:
        with reporters.FancyReporter(out) as reporter:
            out.write(out.bold, out.fg('red'), '\nFAILURES', out.reset)
            for result in sorted(pipe.errors):
                reporter.report(result)
        if not options.ignore_failures:
            return 1

    # push commits upstream
    git.run('push', *options.push_args, cwd=options.repo.location)

    return 0
