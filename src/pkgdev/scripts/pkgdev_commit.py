import argparse
import atexit
import os
import re
import shlex
import subprocess
import tempfile
from collections import defaultdict, UserDict
from dataclasses import dataclass
from itertools import chain, zip_longest

from pkgcheck import reporters, scan
from pkgcore.ebuild.atom import MalformedAtom
from pkgcore.ebuild.atom import atom as atom_cls
from pkgcore.operations import observer as observer_mod
from pkgcore.restrictions import packages
from snakeoil.cli import arghparse
from snakeoil.klass import jit_attr
from snakeoil.mappings import OrderedSet
from snakeoil.osutils import pjoin

from .. import git
from ..mangle import GentooMangler, Mangler
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
commit_opts = commit.add_argument_group('commit options')
commit_opts.add_argument(
    '-m', '--message', type=lambda x: x.strip(),
    help='specify commit message')
commit_opts.add_argument(
    '-M', '--mangle', nargs='?', const=True, action=arghparse.StoreBool,
    help='perform file mangling')
commit_opts.add_argument(
    '-n', '--dry-run', action='store_true',
    help='pretend to create commit')
commit_opts.add_argument(
    '-s', '--scan', action='store_true',
    help='run pkgcheck against staged changes')
commit_opts.add_argument(
    '--ignore-failures', action='store_true',
    help='forcibly create commit with QA errors')

add_actions = commit_opts.add_mutually_exclusive_group()
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

    @property
    def prefix(self):
        return f'{self.atom.unversioned_atom}: '


class GitChanges(UserDict):
    """Mapping of change objects for staged git changes."""

    @jit_attr
    def pkgs(self):
        """Tuple of all package change objects."""
        return tuple(
            change for k, v in self.data.items() for change in v
            if k is PkgChange
        )

    @jit_attr
    def ebuilds(self):
        """Tuple of all ebuild change objects."""
        return tuple(x for x in self.pkgs if x.ebuild)

    @jit_attr
    def paths(self):
        """Tuple of all staged paths."""
        return tuple(x.path for x in chain.from_iterable(self.data.values()))

    def msg_prefix(self):
        """Determine commit message prefix using GLEP 66 as a guide.

        See https://www.gentoo.org/glep/glep-0066.html#commit-messages for
        details.
        """
        # changes limited to a single type
        if len(self.data) == 1:
            change_type, change_objs = next(iter(self.data.items()))
            if len(change_objs) == 1:
                # changes limited to a single object
                change = change_objs[0]
                return change.prefix
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

    def msg_summary(self, repo):
        """Determine commit message summary."""
        # all changes made on the same package
        if len({x.atom.unversioned_atom for x in self.pkgs}) == 1:
            if not self.ebuilds:
                if len(self.pkgs) == 1 and self.pkgs[0].path.endswith('/Manifest'):
                    return 'update Manifest'
            else:
                pkgs = {x.atom: x.status for x in self.ebuilds}
                versions = [x.fullver for x in sorted(pkgs)]
                revbump = any(x.revision for x in pkgs)
                existing_pkgs = repo.match(next(iter(pkgs)).unversioned_atom)
                if len(set(pkgs.values())) == 1:
                    status = next(iter(pkgs.values()))
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

        return ''


def determine_changes(namespace):
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
    _eclass_re = re.compile(r'^eclass/(?P<name>[^/]+\.eclass)$')

    # if no changes exist, exit early
    if not p.stdout:
        commit.error('no staged changes exist')

    data = p.stdout.strip('\x00').split('\x00')
    changes = defaultdict(OrderedSet)
    for status, path in grouper(data, 2):
        path_components = path.split(os.sep)
        if path_components[0] in namespace.repo.categories and len(path_components) > 2:
            if mo := _ebuild_re.match(path):
                # ebuild changes
                try:
                    atom = atom_cls(f"={mo.group('category')}/{mo.group('package')}")
                    changes[PkgChange].add(PkgChange(status, path, atom, ebuild=True))
                except MalformedAtom:
                    continue
            else:
                # non-ebuild package level changes
                atom = atom_cls(os.sep.join(path_components[:2]))
                changes[PkgChange].add(PkgChange(status, path, atom, ebuild=False))
        elif mo := _eclass_re.match(path):
            changes[EclassChange].add(EclassChange(status, path, mo.group('name')))
        else:
            changes[path_components[0]].add(Change(status, path))

    return GitChanges(changes)


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
    msg_prefix = namespace.changes.msg_prefix()

    if message:
        # ignore generated prefix when using custom prefix
        if not re.match(r'^\S+: ', message):
            message = msg_prefix + message
    elif msg_prefix:
        # use generated summary if a generated prefix exists
        msg_summary = namespace.changes.msg_summary(namespace.repo)
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
    namespace.changes = determine_changes(namespace)
    # determine `git commit` args
    namespace.commit_args = determine_commit_args(namespace) + namespace.extended_commit_args

    # mangle files in the gentoo repo by default
    if namespace.mangle is None and namespace.repo.repo_id == 'gentoo':
        namespace.mangle = True

    # determine `pkgcheck scan` args
    namespace.scan_args = ['-v'] * namespace.verbosity
    if namespace.pkgcheck_scan:
        namespace.scan_args.extend(shlex.split(namespace.pkgcheck_scan))
    namespace.scan_args.extend(['--exit', 'GentooCI', '--staged'])


@commit.bind_main_func
def _commit(options, out, err):
    repo = options.repo
    git_add_files = []

    if atoms := {x.atom.unversioned_atom for x in options.changes.ebuilds}:
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
        paths = (pjoin(repo.location, x) for x in options.changes.paths)
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

    # create commit
    git.run('commit', *options.commit_args)

    return 0
