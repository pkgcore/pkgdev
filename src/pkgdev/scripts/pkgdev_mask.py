import datetime
import os
import re
import subprocess
import tempfile
from collections import deque
from dataclasses import dataclass
from itertools import groupby
from operator import itemgetter
from typing import List

from pkgcore.ebuild.atom import MalformedAtom
from pkgcore.ebuild.atom import atom as atom_cls
from pkgcore.ebuild.profiles import ProfileNode
from snakeoil.bash import iter_read_bash
from snakeoil.cli import arghparse
from snakeoil.osutils import pjoin

from .. import git
from .argparsers import cwd_repo_argparser, git_repo_argparser

mask = arghparse.ArgumentParser(
    prog='pkgdev mask', description='mangle profiles/package.mask',
    parents=(cwd_repo_argparser, git_repo_argparser))
mask.add_argument(
    'target', nargs='*',
    help='packages to target',
    docs="""
        Packages matching any of these restrictions will have a mask entry in
        profiles/package.mask added for them. If no target is specified a path
        restriction is created based on the current working directory. In other
        words, if ``pkgdev mask`` is run within an ebuild's directory, all the
        ebuilds within that directory will be masked.
    """)
mask_opts = mask.add_argument_group('mask options')
mask_opts.add_argument(
    '-r', '--rites', nargs='?', const=30, type=arghparse.positive_int,
    help='mark package for last rites')


@mask.bind_final_check
def _mask_validate(parser, namespace):
    atoms = []

    if namespace.target:
        for x in namespace.target:
            try:
                atom = atom_cls(x)
            except MalformedAtom:
                mask.error(f'invalid atom: {x!r}')

            if not namespace.repo.match(atom):
                mask.error(f'no repo matches: {x!r}')
            atoms.append(atom)
    else:
        restrict = namespace.repo.path_restrict(os.getcwd())
        pkgs = {x.unversioned_atom for x in namespace.repo.match(restrict)}
        if len(pkgs) > 1:
            mask.error('not in a package directory')
        atoms.append(next(iter(pkgs)))

    namespace.atoms = atoms


@dataclass(frozen=True)
class Mask:
    """Entry in package.mask file."""
    author: str
    email: str
    date: str
    comment: List[str]
    atoms: List[atom_cls]
    removal: str = None

    def __str__(self):
        lines = [f'# {self.author} <{self.email}> ({self.date})']
        lines.extend(f'# {x}' for x in self.comment)
        if self.removal:
            lines.append(f'# Removal on {self.removal}')
        lines.extend(map(str, self.atoms))
        return '\n'.join(lines)


def consecutive_groups(iterable, ordering=lambda x: x):
    """Return an iterable split into separate, consecutive groups."""
    for k, g in groupby(enumerate(iterable), key=lambda x: x[0] - ordering(x[1])):
        yield map(itemgetter(1), g)


class MaskFile:
    """Object representing the contents of a package.mask file."""

    author_date_re = re.compile(r'^(?P<author>.+) <(?P<email>.+)> \((?P<date>\d{4}-\d{2}-\d{2})\)$')

    def __init__(self, path):
        self.path = path
        self.profile = ProfileNode(os.path.dirname(path))
        self.header = []
        self.masks = deque()

        with open(path) as f:
            lines = f.readlines()
            # determine mask groups by line number
            mask_map = dict(iter_read_bash(path, enum_line=True))
            for mask_lines in map(list, consecutive_groups(mask_map)):
                atoms = [self.profile.eapi_atom(mask_map[x]) for x in mask_lines]
                comment = []
                i = mask_lines[0] - 2
                while line := lines[i].rstrip():
                    if not line.startswith('# '):
                        mask.error(f'invalid mask entry header, lineno {i + 1}: {line!r}')
                    comment.append(line[2:])
                    i -= 1
                if not self.header:
                    self.header = lines[:i + 1]
                comment = list(reversed(comment))
                if mo := self.author_date_re.match(comment[0]):
                    author, email, date = mo.group('author'), mo.group('email'), mo.group('date')
                else:
                    mask.error(f'invalid author, lineno {i + 2}: {comment[0]!r}')
                self.masks.append(Mask(author, email, date, comment[1:], atoms))

    def add(self, mask):
        self.masks.appendleft(mask)

    def write(self):
        with open(self.path, 'w') as f:
            f.write(f'{self}\n')

    def __str__(self):
        return ''.join(self.header) + '\n\n'.join(map(str, self.masks))


def get_comment():
    """Spawn editor to get mask comment."""
    with tempfile.NamedTemporaryFile(mode='w+') as f:
        f.write('\n\n')
        f.write("# Please enter the mask message. Lines starting with '#' will be ignored.")
        f.flush()

        editor = os.environ.get('VISUAL', os.environ.get('EDITOR', 'nano'))
        try:
            subprocess.run([editor, f.name], check=True)
        except subprocess.CalledProcessError:
            mask.error('failed writing mask comment')

        f.seek(0)
        # strip trailing whitespace from lines
        comment = (x.rstrip() for x in f.readlines())
        # strip comments
        comment = (x for x in comment if not x.startswith('#'))
        # strip leading/trailing newlines
        comment = '\n'.join(comment).strip().splitlines()
        if not comment:
            mask.error('empty mask comment')

    return comment


@mask.bind_main_func
def _mask(options, out, err):
    mask_file = MaskFile(pjoin(options.repo.location, 'profiles/package.mask'))
    today = datetime.date.today()

    # pull name/email from git config
    p = git.run('config', 'user.name', stdout=subprocess.PIPE)
    author = p.stdout.strip()
    p = git.run('config', 'user.email', stdout=subprocess.PIPE)
    email = p.stdout.strip()

    # initial args for Mask obj
    mask_args = {
        'author': author,
        'email': email,
        'date': today.isoformat(),
        'comment': get_comment(),
        'atoms': options.atoms,
    }

    if options.rites:
        removal_date = today + datetime.timedelta(days=options.rites)
        mask_args['removal'] = removal_date.isoformat()

    mask_file.add(Mask(**mask_args))
    mask_file.write()

    return 0
