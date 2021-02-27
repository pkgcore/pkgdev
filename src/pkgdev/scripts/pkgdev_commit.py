import os
import re
import subprocess
from collections import defaultdict

from snakeoil.cli import arghparse
from snakeoil.mappings import OrderedSet
from pkgcore.repository import errors as repo_errors


commit = arghparse.ArgumentParser(
    prog='pkgdev commit', description='create git commit')
commit.add_argument(
    '-a', '--all', action='store_true',
    help='automatically stage files')
commit.add_argument(
    '-m', '--message',
    help='specify commit message')
commit.add_argument(
    '-n', '--dry-run', action='store_true',
    help='pretend to create commit')


def commit_msg_prefix(diff_changes):
    """Determine commit message prefix using GLEP 66 as a guide.

    See https://www.gentoo.org/glep/glep-0066.html#commit-messages for
    details.
    """
    # changes limited to a single type
    if len(diff_changes) == 1:
        change_type = next(iter(diff_changes))
        changes = diff_changes[change_type]
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


@commit.bind_main_func
def _commit(options, out, err):
    # determine repo
    try:
        repo = options.domain.find_repo(
            os.getcwd(), config=options.config, configure=False)
    except (repo_errors.InitializationError, IOError) as e:
        commit.error(str(e))

    diff_args = ['--name-only', '-z']
    if not options.all:
        # only check for staged changes
        diff_args.append('--cached')

    try:
        p = subprocess.run(
            ['git', 'diff-index'] + diff_args + ['HEAD'],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            check=True, encoding='utf8')
    except FileNotFoundError:
        commit.error('git not found')
    except subprocess.CalledProcessError as e:
        error = e.stderr.splitlines()[0]
        commit.error(error)

    # if no changes exist, exit early
    if not p.stdout:
        changes = 'staged changes' if not options.all else 'changes'
        out.write(f'{commit.prog}: no {changes} exist')
        return 0

    # parse changes
    diff_changes = defaultdict(OrderedSet)
    for path in p.stdout.strip('\x00').split('\x00'):
        path_components = path.split(os.sep)
        if path_components[0] in repo.categories:
            diff_changes['pkgs'].add(os.sep.join(path_components[:2]))
        else:
            diff_changes[path_components[0]].add(path)

    commit_args = []
    if repo.repo_id == 'gentoo':
        # gentoo repo requires signoffs and signed commits
        commit_args.extend(['--signoff', '--gpg-sign'])
    if options.dry_run:
        commit_args.append('--dry-run')
    if options.verbosity:
        commit_args.append('-v')
    if options.all:
        commit_args.append('--all')

    # determine commit message prefix
    msg_prefix = commit_msg_prefix(diff_changes)

    if options.message:
        # ignore determined prefix when using custom prefix
        if not re.match(r'^\S+: ', options.message):
            message = msg_prefix + options.message
        else:
            message = options.message
        commit_args.extend(['-m', message])
    else:
        # open editor for message using determined prefix
        commit_args.extend(['-m', msg_prefix, '-e'])

    # create commit
    try:
        subprocess.run(
            ['git', 'commit'] + commit_args,
            check=True, stderr=subprocess.PIPE, encoding='utf8')
    except FileNotFoundError:
        commit.error('git not found')
    except subprocess.CalledProcessError as e:
        error = e.stderr.splitlines()[0]
        commit.error(error)

    return 0
