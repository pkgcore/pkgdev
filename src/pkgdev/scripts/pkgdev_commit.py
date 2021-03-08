import argparse
import atexit
import os
import re
import shlex
import subprocess
import tempfile
import textwrap
from collections import defaultdict, deque, UserDict
from dataclasses import dataclass
from itertools import chain

from pkgcheck import reporters, scan
from pkgcore.ebuild.atom import MalformedAtom
from pkgcore.ebuild.atom import atom as atom_cls
from pkgcore.operations import observer as observer_mod
from pkgcore.restrictions import packages
from snakeoil.cli import arghparse
from snakeoil.klass import jit_attr
from snakeoil.mappings import OrderedFrozenSet, OrderedSet
from snakeoil.osutils import pjoin

from .. import git
from ..mangle import GentooMangler, Mangler
from .argparsers import cwd_repo_argparser, git_repo_argparser


class ArgumentParser(arghparse.ArgumentParser):
    """Parse all known arguments, passing unknown arguments to ``git commit``."""

    def parse_known_args(self, args=None, namespace=None):
        namespace, args = super().parse_known_args(args, namespace)
        if namespace.dry_run:
            args.append('--dry-run')
        if namespace.verbosity:
            args.append('-v')
        namespace.commit_args = args
        return namespace, []


commit = ArgumentParser(
    prog='pkgdev commit', description='create git commit',
    parents=(cwd_repo_argparser, git_repo_argparser))
# custom `pkgcheck scan` args used for tests
commit.add_argument('--pkgcheck-scan', help=argparse.SUPPRESS)
commit_opts = commit.add_argument_group('commit options')
commit_opts.add_argument(
    '-n', '--dry-run', action='store_true',
    help='pretend to create commit',
    docs="""
        Perform all actions without creating a commit.
    """)
commit_opts.add_argument(
    '-s', '--scan', action='store_true',
    help='run pkgcheck against staged changes',
    docs="""
        By default, ``pkgdev commit`` doesn't scan for QA errors. This option
        enables using pkgcheck to scan the staged changes for issues, erroring
        out if any failures are found.
    """)
commit_opts.add_argument(
    '--ignore-failures', action='store_true',
    help='forcibly create commit with QA errors',
    docs="""
        When running with the -s/--scan option enabled, ``pkgdev commit`` will
        error out without creating a commit if it detects failure results.
        Specifying this option will force a commit to be created even when QA
        errors are detected.
    """)
commit_opts.add_argument(
    '--mangle', nargs='?', const=True, action=arghparse.StoreBool,
    help='forcibly enable/disable file mangling',
    docs="""
        File mangling automatically modifies the content of relevant staged
        files including updating copyright headers and fixing EOF newlines.

        This is performed by default for the gentoo repo, but can be forcibly
        disabled or enabled as required.
    """)

msg_actions = commit_opts.add_mutually_exclusive_group()
msg_actions.add_argument(
    '-m', '--message', metavar='MSG', action='append',
    help='specify commit message',
    docs="""
        Use a given message as the commit message. If multiple -m options are
        specified, their values are concatenated as separate paragraphs with
        line wrapping at 100 characters.

        Note that the first value will be used for the commit summary and if
        it's empty then a generated summary will be used if available.
    """)
msg_actions.add_argument(
    '-M', '--message-template', metavar='FILE', type=argparse.FileType('r'),
    help='use commit message template from specified file',
    docs="""
        Use content from the given file as a commit message template. The
        commit summary prefix '*: ' is automatically replaced by a generated
        prefix if one exists for the related staged changes.
    """)
msg_actions.add_argument('-F', '--file', help=argparse.SUPPRESS)
msg_actions.add_argument('-t', '--template', help=argparse.SUPPRESS)

add_actions = commit_opts.add_mutually_exclusive_group()
add_actions.add_argument(
    '-u', '--update', dest='git_add_arg', const='--update', action='store_const',
    help='stage all changed files')
add_actions.add_argument(
    '-a', '--all', dest='git_add_arg', const='--all', action='store_const',
    help='stage all changed/new/removed files')


class GitChanges(UserDict):
    """Mapping of change objects for staged git changes."""

    def __init__(self, changes, repo):
        super().__init__(changes)
        self._repo = repo

    @jit_attr
    def pkgs(self):
        """Ordered set of all package change objects."""
        return OrderedFrozenSet(self.data.get(PkgChange, ()))

    @jit_attr
    def ebuilds(self):
        """Ordered set of all ebuild change objects."""
        return OrderedFrozenSet(x for x in self.pkgs if x.ebuild)

    @jit_attr
    def paths(self):
        """Ordered set of all staged paths."""
        return OrderedFrozenSet(x.path for x in chain.from_iterable(self.data.values()))

    @jit_attr
    def prefix(self):
        """Determine commit message prefix using GLEP 66 as a guide.

        See https://www.gentoo.org/glep/glep-0066.html#commit-messages for
        details.
        """
        # changes limited to a single type
        if len(self.data) == 1:
            change_type, change_objs = next(iter(self.data.items()))
            if len(change_objs) == 1:
                # changes limited to a single object
                return change_objs[0].prefix
            else:
                # multiple changes of the same object type
                common_path = os.path.commonpath(x.path for x in change_objs)
                if change_type is PkgChange:
                    if os.sep in common_path:
                        return f'{common_path}: '
                    elif common_path:
                        return f'{common_path}/*: '
                    else:
                        return '*/*: '
                elif common_path:
                    return f'{common_path}: '

        # no prefix used for global changes
        return ''

    @jit_attr
    def summary(self):
        """Determine commit message summary."""
        # all changes made on the same package
        if len({x.atom.unversioned_atom for x in self.pkgs}) == 1:
            if not self.ebuilds:
                if len(self.pkgs) == 1 and self.pkgs[0].path.endswith('/Manifest'):
                    return 'update Manifest'
            else:
                pkgs = {x.atom: x for x in self.ebuilds}
                versions = [x.fullver for x in sorted(pkgs)]
                revbump = any(x.revision for x in pkgs)
                existing_pkgs = self._repo.match(next(iter(pkgs)).unversioned_atom)
                statuses = {x.status for x in pkgs.values()}
                if len(statuses) == 1:
                    status = next(iter(statuses))
                    if status == 'A':
                        if len(existing_pkgs) == len(pkgs):
                            return 'initial import'
                        elif not revbump:
                            msg = f"add {', '.join(versions)}"
                            if len(versions) == 1 or len(msg) <= 50:
                                return msg
                            else:
                                return 'add versions'
                    elif status == 'D':
                        if existing_pkgs:
                            msg = f"drop {', '.join(versions)}"
                            if len(versions) == 1 or len(msg) <= 50:
                                return msg
                            else:
                                return 'drop versions'
                        else:
                            return 'treeclean'
                    elif status == 'R' and len(pkgs) == 1 and not revbump:
                        # handle single, non-revbump `git mv` changes
                        change = next(iter(pkgs.values()))
                        return f'add {change.atom.fullver}, drop {change.old.fullver}'

        return ''


@dataclass(frozen=True)
class Change:
    """Generic file change."""
    status: str
    path: str

    @property
    def prefix(self):
        if os.sep in self.path:
            # use change path's parent directory
            return f'{os.path.dirname(self.path)}: '
        else:
            # use repo root file name
            return f'{self.path}: '


@dataclass(frozen=True)
class EclassChange(Change):
    """Eclass change."""
    name: str

    @property
    def prefix(self):
        return f'{self.name}: '


@dataclass(frozen=True)
class PkgChange(Change):
    """Package change."""
    atom: atom_cls
    ebuild: bool
    old: atom_cls = None

    @property
    def prefix(self):
        return f'{self.atom.unversioned_atom}: '


def determine_changes(options):
    """Determine changes staged in git."""
    # stage changes as requested
    if options.git_add_arg:
        git.run('add', options.git_add_arg, options.cwd)

    # determine staged changes
    p = git.run(
        'diff', '--name-status', '--cached', '-z',
        stdout=subprocess.PIPE)

    # ebuild path regex, validation is handled on instantiation
    _ebuild_re = re.compile(r'^(?P<category>[^/]+)/[^/]+/(?P<package>[^/]+)\.ebuild$')
    _eclass_re = re.compile(r'^eclass/(?P<name>[^/]+\.eclass)$')

    # if no changes exist, exit early
    if not p.stdout:
        commit.error('no staged changes exist')

    data = deque(p.stdout.strip('\x00').split('\x00'))
    changes = defaultdict(OrderedSet)
    while data:
        status = data.popleft()
        old_path = None
        if status.startswith('R'):
            status = 'R'
            old_path = data.popleft()
        path = data.popleft()
        path_components = path.split(os.sep)
        if path_components[0] in options.repo.categories and len(path_components) > 2:
            if mo := _ebuild_re.match(path):
                # ebuild changes
                try:
                    atom = atom_cls(f"={mo.group('category')}/{mo.group('package')}")
                    old = None
                    if status == 'R' and (om := _ebuild_re.match(old_path)):
                        old = atom_cls(f"={om.group('category')}/{om.group('package')}")
                    changes[PkgChange].add(PkgChange(
                        status, path, atom=atom, ebuild=True, old=old))
                except MalformedAtom:
                    continue
            else:
                # non-ebuild package level changes
                atom = atom_cls(os.sep.join(path_components[:2]))
                changes[PkgChange].add(PkgChange(status, path, atom=atom, ebuild=False))
        elif mo := _eclass_re.match(path):
            changes[EclassChange].add(EclassChange(status, path, name=mo.group('name')))
        else:
            changes[path_components[0]].add(Change(status, path))

    return GitChanges(changes, options.repo)


def determine_msg_args(options, changes):
    """Determine message-related arguments used with `git commit`."""
    args = []
    if options.file:
        args.extend(['-F', options.file])
    elif options.template:
        args.extend(['-t', options.template])
    else:
        if options.message_template:
            message = options.message_template.read().splitlines()
            try:
                # TODO: replace with str.removeprefix when py3.8 support dropped
                if message[0].startswith('*: '):
                    message[0] = message[0][3:]
            except IndexError:
                commit.error(f'empty message template: {options.message_template.name!r}')
        else:
            message = [] if options.message is None else options.message

        # determine commit message
        if message:
            # ignore generated prefix when using custom prefix
            if not re.match(r'^\S+: ', message[0]):
                message[0] = changes.prefix + message[0]
        elif changes.prefix:
            # use generated summary if a generated prefix exists
            message.append(changes.prefix + changes.summary)

        if message:
            tmp = tempfile.NamedTemporaryFile(mode='w')
            tmp.write(message[0])
            if len(message) > 1:
                # wrap body paragraphs at 85 chars
                body = ('\n'.join(textwrap.wrap(x, width=85)) for x in message[1:])
                tmp.write('\n\n' + '\n\n'.join(body))
            tmp.flush()

            # force `git commit` to open an editor for uncompleted summary
            if not message[0] or message[0].endswith(' '):
                args.extend(['-t', tmp.name])
            else:
                args.extend(['-F', tmp.name])

            # explicitly close and delete tempfile on exit
            atexit.register(tmp.close)

    return args


@commit.bind_final_check
def _commit_validate(parser, namespace):
    # mangle files in the gentoo repo by default
    if namespace.mangle is None and namespace.repo.repo_id == 'gentoo':
        namespace.mangle = True

    # determine `pkgcheck scan` args
    namespace.scan_args = ['-v'] * namespace.verbosity
    if namespace.pkgcheck_scan:
        namespace.scan_args.extend(shlex.split(namespace.pkgcheck_scan))
    namespace.scan_args.extend(['--exit', 'GentooCI', '--staged'])

    # gentoo repo requires signoffs and signed commits
    if namespace.repo.repo_id == 'gentoo':
        namespace.commit_args.extend(['--signoff', '--gpg-sign'])


@commit.bind_main_func
def _commit(options, out, err):
    repo = options.repo
    git_add_files = []
    # determine changes from staged files
    changes = determine_changes(options)

    if atoms := {x.atom.unversioned_atom for x in changes.ebuilds}:
        # manifest all changed packages
        failed = repo.operations.digests(
            domain=options.domain,
            restriction=packages.OrRestriction(*atoms),
            observer=observer_mod.formatter_output(out))
        if any(failed):
            return 1

        # include existing Manifest files for staging
        manifests = (pjoin(repo.location, f'{x.cpvstr}/Manifest') for x in atoms)
        git_add_files.extend(filter(os.path.exists, manifests))

    # mangle files
    if options.mangle:
        # don't mangle FILESDIR content
        skip_regex = re.compile(rf'^{repo.location}/[^/]+/[^/]+/files/.+$')
        mangler = GentooMangler if repo.repo_id == 'gentoo' else Mangler
        paths = (pjoin(repo.location, x) for x in changes.paths)
        git_add_files.extend(mangler(paths, skip_regex=skip_regex))

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

    # determine message-related args
    args = determine_msg_args(options, changes)
    # create commit
    git.run('commit', *args, *options.commit_args)

    return 0
