from pkgcheck import reporters, scan
from snakeoil.cli import arghparse

from .. import git
from .argparsers import cwd_repo_argparser, git_repo_argparser

push = arghparse.ArgumentParser(
    prog='pkgdev push', description='run QA checks on commits and push them',
    parents=(cwd_repo_argparser, git_repo_argparser))
push.add_argument(
    'remote', nargs='?', default='origin',
    help='remote git repository (default: origin)')
push.add_argument(
    'refspec', nargs='?', default='master',
    help='destination ref to update (default: master)')
push.add_argument(
    '-f', '--force', action='store_true',
    help='forcibly push commits with QA errors')
push.add_argument(
    '-n', '--dry-run', action='store_true',
    help='pretend to push the commits')


@push.bind_delayed_default(1000, 'push_args')
def _push_args(namespace, attr):
    """Determine arguments used with `git push`."""
    args = []
    if namespace.repo.repo_id == 'gentoo':
        # gentoo repo requires signed pushes
        args.append('--signed')
    if namespace.dry_run:
        args.append('--dry-run')

    args.extend([namespace.remote, namespace.refspec])

    setattr(namespace, attr, args)


@push.bind_main_func
def _push(options, out, err):
    # scan commits for QA issues
    pipe = scan(['--exit', 'GentooCI', '--commits'])
    with reporters.FancyReporter(out) as reporter:
        for result in pipe:
            reporter.report(result)

    # fail on errors unless force pushing
    if pipe.errors:
        with reporters.FancyReporter(out) as reporter:
            out.write(out.bold, out.fg('red'), '\nFAILURES', out.reset)
            for result in sorted(pipe.errors):
                reporter.report(result)
        if not options.force:
            return 1

    # push commits upstream
    git.run('push', *options.push_args, cwd=options.repo.location)

    return 0
