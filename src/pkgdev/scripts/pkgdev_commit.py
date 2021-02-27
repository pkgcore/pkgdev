import os
import re
import subprocess
from collections import defaultdict

from pkgcore.ebuild.atom import atom as atom_cls
from pkgcore.operations import observer as observer_mod
from pkgcore.restrictions import packages
from snakeoil.cli import arghparse
from snakeoil.mappings import OrderedSet

from .argparsers import cwd_repo_argparser


commit = arghparse.ArgumentParser(
    prog='pkgdev commit', description='create git commit',
    parents=(cwd_repo_argparser,))
commit.add_argument(
    '-m', '--message',
    help='specify commit message')
commit.add_argument(
    '-n', '--dry-run', action='store_true',
    help='pretend to create commit')

add_actions = commit.add_mutually_exclusive_group()
add_actions.add_argument(
    '-u', '--update', dest='git_add_arg', const='--update', action='store_const',
    help='stage all changed files')
add_actions.add_argument(
    '-a', '--all', dest='git_add_arg', const='--all', action='store_const',
    help='stage all changed/new/removed files')


@commit.bind_delayed_default(1000, 'changes')
def _git_changes(namespace, attr):
    if namespace.git_add_arg:
        try:
            subprocess.run(
                ['git', 'add', namespace.git_add_arg, namespace.cwd],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                check=True, encoding='utf8')
        except subprocess.CalledProcessError as e:
            error = e.stderr.splitlines()[0]
            commit.error(error)

    try:
        p = subprocess.run(
            ['git', 'diff-index', '--name-only', '--cached', '-z', 'HEAD'],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            check=True, encoding='utf8')
    except subprocess.CalledProcessError as e:
        error = e.stderr.splitlines()[0]
        commit.error(error)

    # if no changes exist, exit early
    if not p.stdout:
        commit.error('no staged changes exist')

    # parse changes
    changes = defaultdict(OrderedSet)
    for path in p.stdout.strip('\x00').split('\x00'):
        path_components = path.split(os.sep)
        if path_components[0] in namespace.repo.categories:
            changes['pkgs'].add(os.sep.join(path_components[:2]))
        else:
            changes[path_components[0]].add(path)

    setattr(namespace, attr, changes)


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


@commit.bind_delayed_default(1001, 'commit_args')
def _commit_args(namespace, attr):
    args = []
    if namespace.repo.repo_id == 'gentoo':
        # gentoo repo requires signoffs and signed commits
        args.extend(['--signoff', '--gpg-sign'])
    if namespace.dry_run:
        args.append('--dry-run')
    if namespace.verbosity:
        args.append('-v')

    # determine commit message prefix
    msg_prefix = commit_msg_prefix(namespace.changes)

    if namespace.message:
        # ignore determined prefix when using custom prefix
        if not re.match(r'^\S+: ', namespace.message):
            message = msg_prefix + namespace.message
        else:
            message = namespace.message
        args.extend(['-m', message])
    else:
        # open editor for message using determined prefix
        args.extend(['-m', msg_prefix, '-e'])

    setattr(namespace, attr, args)


@commit.bind_main_func
def _commit(options, out, err):
    # manifest all changed packages
    if pkgs := options.changes.get('pkgs'):
        pkgs = [atom_cls(x) for x in pkgs]
        restriction = packages.OrRestriction(*pkgs)
        failed = options.repo.operations.digests(
            domain=options.domain,
            restriction=restriction,
            observer=observer_mod.null_output())
        if any(failed):
            commit.error('failed generating manifests')

        # stage all Manifest files
        try:
            subprocess.run(
                ['git', 'add'] + [f'{x.cpvstr}/Manifest' for x in pkgs],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                cwd=options.repo.location, check=True, encoding='utf8')
        except subprocess.CalledProcessError as e:
            error = e.stderr.splitlines()[0]
            commit.error(error)

    # create commit
    try:
        subprocess.run(
            ['git', 'commit'] + options.commit_args,
            check=True, stderr=subprocess.PIPE, encoding='utf8')
    except subprocess.CalledProcessError as e:
        error = e.stderr.splitlines()[0]
        commit.error(error)

    return 0
