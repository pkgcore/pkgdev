import argparse
import atexit
import os
import re
import shlex
import subprocess
import tempfile
from collections import defaultdict
from itertools import zip_longest

from pkgcheck import reporters, scan
from pkgcore.ebuild.atom import MalformedAtom
from pkgcore.ebuild.atom import atom as atom_cls
from pkgcore.operations import observer as observer_mod
from pkgcore.restrictions import packages
from snakeoil.cli import arghparse
from snakeoil.mappings import OrderedSet
from snakeoil.osutils import pjoin

from .. import git
from ..mangle import Mangler
from .argparsers import cwd_repo_argparser, git_repo_argparser


class ArgumentParser(arghparse.ArgumentParser):
    """Parse all known arguments, passing unknown arguments to ``git commit``."""

    def parse_known_args(self, args=None, namespace=None):
        namespace, args = super().parse_known_args(args, namespace)
        namespace.extended_commit_args = args
        return namespace, []


commit = ArgumentParser(
    prog='pkgdev commit', description='create git commit',
    parents=(cwd_repo_argparser, git_repo_argparser))
# custom `pkgcheck scan` args used for tests
commit.add_argument('--pkgcheck-scan', help=argparse.SUPPRESS)
commit.add_argument(
    '-m', '--message', type=lambda x: x.strip(),
    help='specify commit message')
commit.add_argument(
    '-M', '--mangle', nargs='?', const=True, action=arghparse.StoreBool,
    help='perform file mangling')
commit.add_argument(
    '-n', '--dry-run', action='store_true',
    help='pretend to create commit')
commit.add_argument(
    '-s', '--scan', action='store_true',
    help='run pkgcheck against staged changes')
commit.add_argument(
    '--ignore-failures', action='store_true',
    help='forcibly create commit with QA errors')

add_actions = commit.add_mutually_exclusive_group()
add_actions.add_argument(
    '-u', '--update', dest='git_add_arg', const='--update', action='store_const',
    help='stage all changed files')
add_actions.add_argument(
    '-a', '--all', dest='git_add_arg', const='--all', action='store_const',
    help='stage all changed/new/removed files')


def grouper(iterable, n, fillvalue=None):
    """Iterate over a given iterable in n-size groups."""
    args = [iter(iterable)] * n
    return zip_longest(*args, fillvalue=fillvalue)


def determine_git_changes(namespace):
    """Determine changes staged in git."""
    # stage changes as requested
    if namespace.git_add_arg:
        git.run('add', namespace.git_add_arg, namespace.cwd)

    # determine staged changes
    p = git.run(
        'diff', '--name-status', '--cached', '-z',
        stdout=subprocess.PIPE)

    # ebuild path regex, validation is handled on instantiation
    _ebuild_re = re.compile(r'^(?P<category>[^/]+)/[^/]+/(?P<package>[^/]+)\.ebuild$')

    # if no changes exist, exit early
    if not p.stdout:
        commit.error('no staged changes exist')

    data = p.stdout.strip('\x00').split('\x00')
    paths = []
    pkgs = {}
    changes = defaultdict(OrderedSet)
    for status, path in grouper(data, 2):
        paths.append(pjoin(namespace.repo.location, path))
        path_components = path.split(os.sep)
        if path_components[0] in namespace.repo.categories and len(path_components) > 2:
            changes['pkgs'].add(os.sep.join(path_components[:2]))
            if mo := _ebuild_re.match(path):
                try:
                    atom = atom_cls(f"={mo.group('category')}/{mo.group('package')}")
                    pkgs[atom] = status
                except MalformedAtom:
                    pass
        else:
            changes[path_components[0]].add(path)

    namespace.paths = paths
    namespace.pkgs = pkgs
    return changes


def commit_msg_prefix(git_changes):
    """Determine commit message prefix using GLEP 66 as a guide.

    See https://www.gentoo.org/glep/glep-0066.html#commit-messages for
    details.
    """
    # changes limited to a single type
    if len(git_changes) == 1:
        change_type = next(iter(git_changes))
        changes = git_changes[change_type]
        if len(changes) == 1:
            change = changes[0]
            # changes limited to a single object
            if change_type == 'pkgs':
                return f'{change}: '
            elif change_type == 'eclass' and change.endswith('.eclass'):
                # use eclass file name
                return f'{os.path.basename(change)}: '
            else:
                # use change path's parent directory
                return f'{os.path.dirname(change)}: '
        else:
            # multiple changes of the same object type
            common_path = os.path.commonpath(changes)
            if change_type == 'pkgs':
                if common_path:
                    return f'{common_path}/*: '
                else:
                    return '*/*: '
            else:
                return f'{common_path}: '

    # no prefix used for global changes
    return ''


def commit_msg_summary(repo, pkgs):
    """Determine commit message summary."""
    if len({x.unversioned_atom for x in pkgs}) == 1:
        # all changes made on the same package
        versions = [x.version for x in sorted(pkgs)]
        atom = next(iter(pkgs)).unversioned_atom
        existing_pkgs = repo.match(atom)
        if len(set(pkgs.values())) == 1:
            status = next(iter(pkgs.values()))
            if status == 'A':
                if len(existing_pkgs) == len(pkgs):
                    return 'initial import'
                else:
                    msg = f"bump {', '.join(versions)}"
                    if len(msg) <= 50:
                        return msg
                    else:
                        return 'bump versions'
            elif status == 'D':
                if existing_pkgs:
                    msg = f"drop {', '.join(versions)}"
                    if len(msg) <= 50:
                        return msg
                    else:
                        return 'drop versions'
                else:
                    return 'treeclean'
    return ''


def determine_commit_args(namespace):
    """Determine arguments used with `git commit`."""
    args = []
    if namespace.repo.repo_id == 'gentoo':
        # gentoo repo requires signoffs and signed commits
        args.extend(['--signoff', '--gpg-sign'])
    if namespace.dry_run:
        args.append('--dry-run')
    if namespace.verbosity:
        args.append('-v')

    # determine commit message
    message = namespace.message
    msg_prefix = commit_msg_prefix(namespace.changes)

    if message:
        # ignore generated prefix when using custom prefix
        if not re.match(r'^\S+: ', message):
            message = msg_prefix + message
    elif msg_prefix:
        # use generated summary if a generated prefix exists
        msg_summary = commit_msg_summary(namespace.repo, namespace.pkgs)
        message = msg_prefix + msg_summary

    if message:
        tmp = tempfile.NamedTemporaryFile(mode='w')
        tmp.write(message)
        tmp.flush()
        if message.endswith(' '):
            # force `git commit` to respect trailing prefix whitespace
            args.extend(['-t', tmp.name])
        else:
            args.extend(['-F', tmp.name])
        # explicitly close and delete tempfile on exit
        atexit.register(tmp.close)

    return args


@commit.bind_final_check
def _commit_validate(parser, namespace):
    # determine changes from staged files
    namespace.changes = determine_git_changes(namespace)
    # determine `git commit` args
    namespace.commit_args = determine_commit_args(namespace) + namespace.extended_commit_args

    # mangle files in the gentoo repo by default
    if namespace.mangle is None and namespace.repo.repo_id == 'gentoo':
        namespace.mangle = True

    # determine `pkgcheck scan` args
    namespace.scan_args = []
    if namespace.pkgcheck_scan:
        namespace.scan_args.extend(shlex.split(namespace.pkgcheck_scan))
    namespace.scan_args.extend(['--exit', 'GentooCI', '--staged'])


@commit.bind_main_func
def _commit(options, out, err):
    repo = options.repo
    git_add_files = []

    if pkgs := options.changes.get('pkgs'):
        pkgs = [atom_cls(x) for x in pkgs]
        # manifest all changed packages
        failed = repo.operations.digests(
            domain=options.domain,
            restriction=packages.OrRestriction(*pkgs),
            observer=observer_mod.formatter_output(out))
        if any(failed):
            return 1

        # include existing Manifest files for staging
        manifests = (pjoin(repo.location, f'{x.cpvstr}/Manifest') for x in pkgs)
        git_add_files.extend(filter(os.path.exists, manifests))

    # mangle files
    if options.mangle:
        git_add_files.extend(Mangler(options.repo, options.paths))

    # stage modified files
    if git_add_files:
        git.run('add', *git_add_files, cwd=repo.location)

    # scan staged changes for QA issues if requested
    if options.scan:
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

    # create commit
    git.run('commit', *options.commit_args)

    return 0
