import os
import re
import shlex
import subprocess
import tempfile
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
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
    prog='pkgdev mask', description='mask packages',
    parents=(cwd_repo_argparser, git_repo_argparser))
mask.add_argument(
    'targets', metavar='TARGET', nargs='*',
    help='package to mask',
    docs="""
        Packages matching any of these restrictions will have a mask entry in
        profiles/package.mask added for them. If no target is specified a path
        restriction is created based on the current working directory. In other
        words, if ``pkgdev mask`` is run within an ebuild's directory, all the
        ebuilds within that directory will be masked.
    """)
mask_opts = mask.add_argument_group('mask options')
mask_opts.add_argument(
    '-r', '--rites', metavar='DAYS', nargs='?', const=30, type=arghparse.positive_int,
    help='mark for last rites',
    docs="""
        Mark a mask entry for last rites. This defaults to 30 days until
        package removal but accepts an optional argument for the number of
        days.
    """)


@mask.bind_final_check
def _mask_validate(parser, namespace):
    atoms = []

    if namespace.targets:
        for x in namespace.targets:
            if os.path.exists(x) and x.endswith('.ebuild'):
                restrict = namespace.repo.path_restrict(x)
                pkg = next(namespace.repo.itermatch(restrict))
                atom = pkg.versioned_atom
            else:
                try:
                    atom = atom_cls(x)
                except MalformedAtom:
                    mask.error(f'invalid atom: {x!r}')
                if not namespace.repo.match(atom):
                    mask.error(f'no repo matches: {x!r}')
            atoms.append(atom)
    else:
        restrict = namespace.repo.path_restrict(os.getcwd())
        # repo, category, and package level restricts
        if len(restrict) != 3:
            mask.error('not in a package directory')
        pkg = next(namespace.repo.itermatch(restrict))
        atoms.append(pkg.unversioned_atom)

    namespace.atoms = sorted(atoms)


@dataclass(frozen=True)
class Mask:
    """Entry in package.mask file."""
    author: str
    email: str
    date: str
    comment: List[str]
    atoms: List[atom_cls]

    _removal_re = re.compile(r'^Removal: (?P<date>\d{4}-\d{2}-\d{2})')

    def __str__(self):
        lines = [f'# {self.author} <{self.email}> ({self.date})']
        lines.extend(f'# {x}' for x in self.comment)
        lines.extend(map(str, self.atoms))
        return '\n'.join(lines)

    @property
    def removal(self):
        """Pull removal date from comment."""
        if mo := self._removal_re.match(self.comment[-1]):
            return mo.group('date')
        return None


def consecutive_groups(iterable, ordering=lambda x: x):
    """Return an iterable split into separate, consecutive groups."""
    for k, g in groupby(enumerate(iterable), key=lambda x: x[0] - ordering(x[1])):
        yield map(itemgetter(1), g)


class MaskFile:
    """Object representing the contents of a package.mask file."""

    attribution_re = re.compile(r'^(?P<author>.+) <(?P<email>.+)> \((?P<date>\d{4}-\d{2}-\d{2})\)$')

    def __init__(self, path):
        self.path = path
        self.profile = ProfileNode(os.path.dirname(path))
        self.header = []
        self.masks = deque()

        # parse existing mask entries
        try:
            self.parse()
        except FileNotFoundError:
            pass

    def parse(self):
        """Parse the given file into Mask objects."""
        with open(self.path) as f:
            lines = f.readlines()

        # determine mask groups by line number
        mask_map = dict(iter_read_bash(self.path, enum_line=True))
        for mask_lines in map(list, consecutive_groups(mask_map)):
            # use profile's EAPI setting to coerce supported masks
            atoms = [self.profile.eapi_atom(mask_map[x]) for x in mask_lines]

            # pull comment lines above initial mask entry line
            comment = []
            i = mask_lines[0] - 2
            while i >= 0 and (line := lines[i].rstrip()):
                if not line.startswith('# '):
                    mask.error(f'invalid mask entry header, lineno {i + 1}: {line!r}')
                comment.append(line[2:])
                i -= 1
            if not self.header:
                self.header = lines[:i + 1]
            comment = list(reversed(comment))

            # pull attribution data from first comment line
            if mo := self.attribution_re.match(comment[0]):
                author, email, date = mo.group('author'), mo.group('email'), mo.group('date')
            else:
                mask.error(f'invalid author, lineno {i + 2}: {comment[0]!r}')

            self.masks.append(Mask(author, email, date, comment[1:], atoms))

    def add(self, mask):
        """Add a new mask to the file."""
        self.masks.appendleft(mask)

    def write(self):
        """Serialize the registered masks back to the related file."""
        with open(self.path, 'w') as f:
            f.write(f'{self}\n')

    def __str__(self):
        return ''.join(self.header) + '\n\n'.join(map(str, self.masks))


def get_comment():
    """Spawn editor to get mask comment."""
    tmp = tempfile.NamedTemporaryFile(mode='w')
    tmp.write("\n\n# Please enter the mask message. Lines starting with '#' will be ignored.\n")
    tmp.flush()

    editor = shlex.split(os.environ.get('VISUAL', os.environ.get('EDITOR', 'nano')))
    try:
        subprocess.run(editor + [tmp.name], check=True)
    except subprocess.CalledProcessError:
        mask.error('failed writing mask comment')
    except FileNotFoundError:
        mask.error(f'nonexistent editor: {editor[0]!r}')

    with open(tmp.name) as f:
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
    today = datetime.now(timezone.utc)

    # pull name/email from git config
    p = git.run('config', 'user.name', stdout=subprocess.PIPE)
    author = p.stdout.strip()
    p = git.run('config', 'user.email', stdout=subprocess.PIPE)
    email = p.stdout.strip()

    # initial args for Mask obj
    mask_args = {
        'author': author,
        'email': email,
        'date': today.strftime('%Y-%m-%d'),
        'comment': get_comment(),
        'atoms': options.atoms,
    }

    if options.rites:
        removal_date = today + timedelta(days=options.rites)
        removal = removal_date.strftime('%Y-%m-%d')
        mask_args['comment'].append(f'Removal: {removal}')

    mask_file.add(Mask(**mask_args))
    mask_file.write()

    return 0
