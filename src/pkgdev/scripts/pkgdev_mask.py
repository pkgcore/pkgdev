import json
import os
import re
import shlex
import subprocess
import tempfile
import textwrap
import urllib.request as urllib
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
from snakeoil.strings import pluralism

from .. import git
from .argparsers import cwd_repo_argparser, git_repo_argparser, BugzillaApiKey

mask = arghparse.ArgumentParser(
    prog="pkgdev mask",
    description="mask packages",
    parents=(cwd_repo_argparser, git_repo_argparser),
)
BugzillaApiKey.mangle_argparser(mask)
mask.add_argument(
    "targets",
    metavar="TARGET",
    nargs="*",
    help="package to mask",
    docs="""
        Packages matching any of these restrictions will have a mask entry in
        profiles/package.mask added for them. If no target is specified a path
        restriction is created based on the current working directory. In other
        words, if ``pkgdev mask`` is run within an ebuild's directory, all the
        ebuilds within that directory will be masked.
    """,
)
mask_opts = mask.add_argument_group("mask options")
mask_opts.add_argument(
    "-r",
    "--rites",
    metavar="DAYS",
    nargs="?",
    const=30,
    type=arghparse.positive_int,
    help="mark for last rites",
    docs="""
        Mark a mask entry for last rites. This defaults to 30 days until
        package removal but accepts an optional argument for the number of
        days.
    """,
)
mask_opts.add_argument(
    "-b",
    "--bug",
    "--bugs",
    dest="bugs",
    action=arghparse.CommaSeparatedValuesAppend,
    default=[],
    help="reference bug in the mask comment",
    docs="""
        Add a reference to a bug in the mask comment. May be specified multiple
        times to reference multiple bugs.
    """,
)
mask_opts.add_argument(
    "--email",
    action="store_true",
    help="spawn email composer with prepared email for sending to mailing lists",
    docs="""
        Spawn user's preferred email composer with a prepared email for
        sending a last rites message to Gentoo's mailing list (``gentoo-dev``
        and ``gentoo-dev-announce``). The user should manually set the Reply-to
        field for the message to be accepted by ``gentoo-dev-announce``.

        For spawning the preferred email composer, the ``xdg-email`` tool from
        ``x11-misc/xdg-utils`` package.
    """,
)
mask_opts.add_argument(
    "--file-bug",
    action="store_true",
    help="file a last-rite bug",
    docs="""
        Files a last-rite bug for the masked package, which blocks listed
        reference bugs. ``PMASKED`` keyword is added all all referenced bugs.
    """,
)


@mask.bind_final_check
def _mask_validate(parser, namespace):
    atoms = set()
    maintainers = set()

    try:
        namespace.bugs = list(map(int, dict.fromkeys(namespace.bugs)))
    except ValueError:
        parser.error("argument -b/--bug: invalid integer value")
    if min(namespace.bugs, default=1) < 1:
        parser.error("argument -b/--bug: must be >= 1")

    if not namespace.rites and namespace.file_bug:
        mask.error("bug filing requires last rites")
    if namespace.file_bug and not namespace.api_key:
        mask.error("bug filing requires a Bugzilla API key")

    if namespace.email and not namespace.rites:
        mask.error("last rites required for email support")

    if namespace.targets:
        for x in namespace.targets:
            if os.path.exists(x) and x.endswith(".ebuild"):
                restrict = namespace.repo.path_restrict(x)
                pkg = next(namespace.repo.itermatch(restrict))
                atom = pkg.versioned_atom
                maintainers.update(maintainer.email for maintainer in pkg.maintainers)
            else:
                try:
                    atom = atom_cls(x)
                except MalformedAtom:
                    mask.error(f"invalid atom: {x!r}")
                if pkgs := namespace.repo.match(atom):
                    maintainers.update(
                        maintainer.email for pkg in pkgs for maintainer in pkg.maintainers
                    )
                else:
                    mask.error(f"no repo matches: {x!r}")
            atoms.add(atom)
    else:
        restrict = namespace.repo.path_restrict(os.getcwd())
        # repo, category, and package level restricts
        if len(restrict) != 3:
            mask.error("not in a package directory")
        pkg = next(namespace.repo.itermatch(restrict))
        atoms.add(pkg.unversioned_atom)
        maintainers.update(maintainer.email for maintainer in pkg.maintainers)

    namespace.atoms = sorted(atoms)
    namespace.maintainers = sorted(maintainers) or ["maintainer-needed@gentoo.org"]


@dataclass(frozen=True)
class Mask:
    """Entry in package.mask file."""

    author: str
    email: str
    date: str
    comment: List[str]
    atoms: List[atom_cls]

    _removal_re = re.compile(r"^Removal: (?P<date>\d{4}-\d{2}-\d{2})")

    def __str__(self):
        lines = [f"# {self.author} <{self.email}> ({self.date})"]
        lines.extend(f"# {x}" if x else "#" for x in self.comment)
        lines.extend(map(str, self.atoms))
        return "\n".join(lines)

    @property
    def removal(self):
        """Pull removal date from comment."""
        if mo := self._removal_re.match(self.comment[-1]):
            return mo.group("date")
        return None


def consecutive_groups(iterable, ordering=lambda x: x):
    """Return an iterable split into separate, consecutive groups."""
    for k, g in groupby(enumerate(iterable), key=lambda x: x[0] - ordering(x[1])):
        yield map(itemgetter(1), g)


class MaskFile:
    """Object representing the contents of a package.mask file."""

    attribution_re = re.compile(r"^(?P<author>.+) <(?P<email>.+)> \((?P<date>\d{4}-\d{2}-\d{2})\)$")

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
                if not line.startswith("# ") and line != "#":
                    mask.error(f"invalid mask entry header, lineno {i + 1}: {line!r}")
                comment.append(line[2:])
                i -= 1
            if not self.header:
                self.header = lines[: i + 1]
            comment = list(reversed(comment))

            # pull attribution data from first comment line
            if mo := self.attribution_re.match(comment[0]):
                author, email, date = mo.group("author"), mo.group("email"), mo.group("date")
            else:
                mask.error(f"invalid author, lineno {i + 2}: {comment[0]!r}")

            self.masks.append(Mask(author, email, date, comment[1:], atoms))

    def add(self, mask):
        """Add a new mask to the file."""
        self.masks.appendleft(mask)

    def write(self):
        """Serialize the registered masks back to the related file."""
        with open(self.path, "w") as f:
            f.write(f"{self}\n")

    def __str__(self):
        return "".join(self.header) + "\n\n".join(map(str, self.masks))


def get_comment():
    """Spawn editor to get mask comment."""
    tmp = tempfile.NamedTemporaryFile(mode="w")
    tmp.write(
        textwrap.dedent(
            """

                # Please enter the mask message. Lines starting with '#' will be ignored.
                #
                # If last-rite was requested, it would be added automatically.
                #
                # For rules on writing mask messages, see GLEP-84:
                #   https://glep.gentoo.org/glep-0084.html
                #
                # Example:
                #
                # Doesn't work with new libfoo. Upstream dead, gtk-1, smells
                # funny.
            """
        )
    )
    tmp.flush()

    editor = shlex.split(os.environ.get("VISUAL", os.environ.get("EDITOR", "nano")))
    try:
        subprocess.run(editor + [tmp.name], check=True)
    except subprocess.CalledProcessError:
        mask.error("failed writing mask comment")
    except FileNotFoundError:
        mask.error(f"nonexistent editor: {editor[0]!r}")

    with open(tmp.name) as f:
        # strip trailing whitespace from lines
        comment = (x.rstrip() for x in f.readlines())
    # strip comments
    comment = (x for x in comment if not x.startswith("#"))
    # strip leading/trailing newlines
    comment = "\n".join(comment).strip().splitlines()
    if not comment:
        mask.error("empty mask comment")
    return comment


def message_removal_notice(bugs: list[int], rites: int):
    summary = []
    if rites:
        summary.append(f"Removal on {datetime.now(timezone.utc) + timedelta(days=rites):%Y-%m-%d}.")
    if bugs:
        # Bug(s) #A, #B, #C
        bug_list = ", ".join(f"#{b}" for b in bugs)
        s = pluralism(bugs)
        summary.append(f"Bug{s} {bug_list}.")
    return "  ".join(summary)


def file_last_rites_bug(options, message: str) -> int:
    summary = f"{', '.join(map(str, options.atoms))}: removal"
    if len(summary) > 90 and len(options.atoms) > 1:
        summary = f"{options.atoms[0]} and friends: removal"
    request_data = dict(
        Bugzilla_api_key=options.api_key,
        product="Gentoo Linux",
        component="Current packages",
        version="unspecified",
        summary=summary,
        description="\n".join([*message, "", "package list:", *map(str, options.atoms)]).strip(),
        keywords=["PMASKED"],
        assigned_to=options.maintainers[0],
        cc=options.maintainers[1:] + ["treecleaner@gentoo.org"],
        deadline=(datetime.now(timezone.utc) + timedelta(days=options.rites)).strftime("%Y-%m-%d"),
        blocks=list(options.bugs),
    )
    request = urllib.Request(
        url="https://bugs.gentoo.org/rest/bug",
        data=json.dumps(request_data).encode("utf-8"),
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    with urllib.urlopen(request, timeout=30) as response:
        reply = json.loads(response.read().decode("utf-8"))
    return int(reply["id"])


def update_bugs_pmasked(api_key: str, bugs: list[int]):
    if not bugs:
        return True
    request_data = dict(
        Bugzilla_api_key=api_key,
        ids=bugs,
        keywords=dict(add=["PMASKED"]),
    )
    request = urllib.Request(
        url=f"https://bugs.gentoo.org/rest/bug/{bugs[0]}",
        data=json.dumps(request_data).encode("utf-8"),
        method="PUT",
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    with urllib.urlopen(request, timeout=30) as response:
        return response.status == 200


def send_last_rites_email(m: Mask, subject_prefix: str):
    try:
        atoms = ", ".join(map(str, m.atoms))
        subprocess.run(
            args=[
                "xdg-email",
                "--utf8",
                "--cc",
                "gentoo-dev@lists.gentoo.org",
                "--subject",
                f"{subject_prefix}: {atoms}",
                "--body",
                str(m),
                "gentoo-dev-announce@lists.gentoo.org",
            ],
            check=True,
        )
    except subprocess.CalledProcessError:
        mask.error("failed opening email composer")


@mask.bind_main_func
def _mask(options, out, err):
    mask_file = MaskFile(pjoin(options.repo.location, "profiles/package.mask"))
    today = datetime.now(timezone.utc)

    # pull name/email from git config
    p = git.run("config", "user.name", stdout=subprocess.PIPE)
    author = p.stdout.strip()
    p = git.run("config", "user.email", stdout=subprocess.PIPE)
    email = p.stdout.strip()

    message = get_comment()
    if options.file_bug:
        if bug_no := file_last_rites_bug(options, message):
            out.write(out.fg("green"), f"filed bug https://bugs.gentoo.org/{bug_no}", out.reset)
            out.flush()
            if not update_bugs_pmasked(options.api_key, options.bugs):
                err.write(err.fg("red"), "failed to update referenced bugs", err.reset)
                err.flush()
            options.bugs.insert(0, bug_no)
    if removal := message_removal_notice(options.bugs, options.rites):
        message.append(removal)

    m = Mask(
        author=author,
        email=email,
        date=today.strftime("%Y-%m-%d"),
        comment=message,
        atoms=options.atoms,
    )
    mask_file.add(m)
    mask_file.write()

    if options.email:
        send_last_rites_email(m, "Last rites")

    return 0
