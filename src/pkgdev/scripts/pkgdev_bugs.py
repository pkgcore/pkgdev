"""Automatic bugs filer"""

import contextlib
import os
import shlex
import subprocess
import sys
import tempfile
import tomllib
import urllib.request as urllib
from collections import defaultdict
from datetime import UTC, datetime
from functools import partial
from itertools import chain
from os.path import join as pjoin

from pkgcheck import const as pkgcheck_const
from pkgcheck.addons import ArchesAddon, init_addon
from pkgcheck.addons.git import GitAddedRepo, GitAddon, GitModifiedRepo
from pkgcheck.addons.profiles import ProfileAddon
from pkgcheck.checks import stablereq, visibility
from pkgcheck.scripts import argparse_actions
from pkgcore.bugzilla import (
    Bug,
    BugCategory,
    BugQuery,
    BugUpdate,
    Bugzilla,
    BugzillaError,
    ListChange,
    NewBug,
    PackageList,
)
from pkgcore.bugzilla.apikey import BugzillaApiKey
from pkgcore.bugzilla.changes import summarise
from pkgcore.ebuild.atom import atom
from pkgcore.ebuild.ebuild_src import package
from pkgcore.ebuild.errors import MalformedAtom
from pkgcore.ebuild.keywording import suggested_keywords
from pkgcore.ebuild.misc import sort_keywords
from pkgcore.ebuild.repo_objs import LocalMetadataXml, ProjectsXml
from pkgcore.package.mutated import MutatedPkg
from pkgcore.repository import multiplex
from pkgcore.restrictions import boolean, packages, values
from pkgcore.util import commandline, parserestrict
from snakeoil.cli import arghparse
from snakeoil.cli.input import userquery
from snakeoil.data_source import bytes_data_source
from snakeoil.formatters import Formatter

from .. import __version__
from ..cli import ArgumentParser
from .argparsers import _determine_cwd_repo, cwd_repo_argparser

_CATEGORY_BY_SUFFIX = {x.summary_suffix: x for x in BugCategory}


class StoreTargetArches(commandline.StoreTarget):
    """``StoreTarget`` variant accepting trailing arches after each atom.

    A target may carry a whitespace separated list of arches after the atom,
    as a bug's package list does, e.g. ``=cat/pkg-1.0 amd64 x86``. This produces 3-tuples
    ``(token, restriction, arches)`` instead of the usual ``(token, restriction)``.

    Note: this reimplements ``StoreTarget.__call__`` (it cannot inject the arch
    splitting otherwise), supporting only the subset of features used by the
    ``pkgdev bugs`` targets argument (package sets and stdin ``-``).
    """

    def __call__(self, parser, namespace, values, option_string=None):
        if self.use_sets:
            setattr(namespace, self.use_sets, [])

        if isinstance(values, str):
            values = [values]
        elif values is not None and len(values) == 1 and values[0] == "-":
            if not sys.stdin.isatty():
                values = [x.strip() for x in sys.stdin.readlines() if x.strip()]
                # reassign stdin to allow interactivity (currently only works for unix)
                sys.stdin = open("/dev/tty")  # noqa: SIM115
            else:
                parser.error("'-' is only valid when piping data in")

        result = []
        for token in values:
            if self.use_sets and token.startswith("@"):
                namespace.sets.append(token[1:])
                continue
            atom_str, *arches = token.split()
            try:
                restriction = parserestrict.parse_match(atom_str)
            except parserestrict.ParseError as e:
                parser.error(e)
            result.append((atom_str, restriction, frozenset(arches)))
        setattr(namespace, self.dest, result)


bugs = ArgumentParser(
    prog="pkgdev bugs",
    description=__doc__,
    verbose=False,
    quiet=False,
    parents=(cwd_repo_argparser,),
    docs="""
        Automatically file stabilization (``STABLEREQ``) and keywording
        (``KEYWORDREQ``) bugs on Gentoo's Bugzilla, resolving and linking the
        dependency graph between the created bugs.

        The mode is selected with ``--stablereq`` (the default) or
        ``--keywording``. For stabilization the target arches are derived from
        the package's current ``~arch`` keywords. For keywording they are
        derived from the keywords other versions of the package carry (restoring
        dropped keywords, i.e. rekeywording); when they cannot be derived they
        must be given explicitly as a whitespace separated list after the atom.

        While filing stabilization bugs, dependencies that are not yet keyworded
        on a required arch get a keywording bug filed automatically, and the
        stabilization bug is made to depend on it.

        Examples::

            # file a stablereq bug, arches taken from the ~arch keywords
            pkgdev bugs '=dev-libs/foo-1.2.3'

            # rekeyword a version, restoring the keywords other versions have
            pkgdev bugs --keywording '=dev-libs/foo-1.2.3'

            # keyword a package for an explicit list of arches
            pkgdev bugs --keywording '=dev-libs/foo-1.2.3 ppc64 riscv'

            # file stablereq bugs for all packages maintained by an address
            pkgdev bugs --find-by-maintainer foo@gentoo.org

            # ... limited to those with an active StableRequest result
            pkgdev bugs --find-by-maintainer foo@gentoo.org --filter-stablereqs

            # file stablereq bugs for all packages inside category "dev-libs" with an active StableRequest
            pkgdev bugs --filter-stablereqs "dev-libs/*"
    """,
)
BugzillaApiKey.mangle_argparser(bugs)
bugs.add_argument(
    "targets",
    metavar="target",
    nargs="*",
    action=StoreTargetArches,
    use_sets="sets",
    help="extended atom matching of packages",
    docs="""
        Extended atom matching of packages. Each target may carry a whitespace
        separated list of arches after the atom, e.g. ``=cat/pkg-1.0 amd64 x86``,
        which is required for keywording packages where the arches cannot be
        derived automatically.
    """,
)
bugs.add_argument(
    "--dot",
    help="path file where to save the graph in dot format",
)
bugs.add_argument(
    "--edit-graph",
    action="store_true",
    help="open editor to modify the graph before filing bugs",
    docs="""
        When this argument is passed, pkgdev will open the graph in the editor
        (either ``$VISUAL`` or ``$EDITOR``) before filing bugs. The graph is
        represented in TOML format. After saving and exiting the editor, the
        tool would use the graph from the file to file bugs.
    """,
)
bugs.add_argument(
    "--auto-cc-arches",
    action=arghparse.CommaSeparatedNegationsAppend,
    default=([], []),
    metavar="EMAIL",
    help="automatically add CC-ARCHES for the listed email addresses",
    docs="""
        Comma separated list of email addresses, for which automatically add
        CC-ARCHES if one of the maintainers matches the email address. If the
        package is maintainer-needed, always add CC-ARCHES.
    """,
)
bugs.add_argument(
    "--find-by-maintainer",
    action=arghparse.CommaSeparatedNegationsAppend,
    default=([], []),
    metavar="EMAIL",
    help="collect all packages maintained by the listed email addresses",
    docs="""
        Comma separated list of email addresses, for which pkgdev will collect
        all packages maintained by.

        Note that this flag requires to go over all packages in the repository
        to find matches, which can be slow (between 1 to 3 seconds).
    """,
)
bugs.add_argument(
    "--projects",
    action="store_true",
    help="include packages maintained by projects",
    docs="""
        Include packages maintained by projects, whose members include the
        emails of maintainers passed to ``--find-by-maintainer``.

        Note that this flag requires to fetch the ``projects.xml`` file from
        ``https://api.gentoo.org``.
    """,
)
bugs.add_argument(
    "--filter-stablereqs",
    action="store_true",
    help="filter targets for packages with active StableRequest result",
    docs="""
        Filter targets passed to pkgdev (command line, stabilization groups,
        maintainer search, stdin) for packages with active ``StableRequest``
        result.
    """,
)
bugs.add_argument(
    "--blocks",
    metavar="BUG",
    action=arghparse.CommaSeparatedValuesAppend,
    default=[],
    help="bugs which should be blocked by newly created bugs",
    docs="""
        Collection of bug ids which should be blocked by newly created bugs.
        Only bugs created for passed targets would be blockers, excluding other
        bugs which were created as dependencies.
    """,
)

bugs.add_argument(
    "--cache",
    action=argparse_actions.CacheNegations,
    help=arghparse.SUPPRESS,
)
bugs.add_argument(
    "--cache-dir",
    type=arghparse.create_dir,
    default=pkgcheck_const.USER_CACHE_DIR,
    help=arghparse.SUPPRESS,
)
bugs_state = bugs.add_mutually_exclusive_group()
bugs_state.add_argument(
    "-s",
    "--stablereq",
    dest="keywording",
    default=None,
    action="store_false",
    help="File stable request bugs",
)
bugs_state.add_argument(
    "-k",
    "--keywording",
    dest="keywording",
    default=None,
    action="store_true",
    help="File rekeywording bugs",
)

bugs.plugin = bugs
ArchesAddon.mangle_argparser(bugs)
GitAddon.mangle_argparser(bugs)
ProfileAddon.mangle_argparser(bugs)
stablereq.StableRequestCheck.mangle_argparser(bugs)


@bugs.bind_delayed_default(1500, "target_repo")
def _validate_args(namespace, attr):
    _determine_cwd_repo(bugs, namespace)
    setattr(namespace, attr, namespace.repo)
    namespace.verbosity = 1
    namespace.search_repo = (search_repo := multiplex.tree(*namespace.repo.trees))
    namespace.gentoo_repo = search_repo
    namespace.query_caching_freq = "package"


@bugs.bind_final_check
def _validate_args(parser, namespace):
    if namespace.keywording and namespace.filter_stablereqs:
        parser.error("--keywording is incompatible with --filter-stablereqs")
    namespace.category = BugCategory.KEYWORDREQ if namespace.keywording else BugCategory.STABLEREQ
    namespace.bugzilla = Bugzilla(namespace.api_key, user_agent=f"pkgdev-bugs/{__version__}")


def parse_atom(pkg: str):
    try:
        return atom(pkg)
    except MalformedAtom as exc:
        try:
            return atom(f"={pkg}")
        except MalformedAtom:
            raise exc


@contextlib.contextmanager
def _naming(what: str):
    """Say what was being filed when bugzilla rejects a change."""
    try:
        yield
    except BugzillaError as exc:
        raise BugzillaError(f"{what}: {exc}") from exc


class GraphNode:
    __slots__ = ("bugno", "category", "cc_arches", "edges", "obsoletes", "pkgs", "summary")

    def __init__(
        self,
        pkgs: tuple[tuple[package, set[str]], ...],
        category: BugCategory = BugCategory.STABLEREQ,
        bugno=None,
    ):
        self.pkgs = pkgs
        self.category = category
        self.edges: set[GraphNode] = set()
        self.bugno = bugno
        self.summary = ""
        self.cc_arches = None
        self.obsoletes: set[int] = set()

    @property
    def is_keywordreq(self):
        return self.category is BugCategory.KEYWORDREQ

    def __eq__(self, __o: object):
        return self is __o

    def __hash__(self):
        return hash(id(self))

    def __str__(self):
        return ", ".join(str(pkg.versioned_atom) for pkg, _ in self.pkgs)

    def __repr__(self):
        return str(self)

    def lines(self):
        # keywordreq bugs are usually version-less ; stablereq bugs are version-pinned
        for pkg, keywords in self.pkgs:
            if self.is_keywordreq:
                atom_str = pkg.unversioned_atom
                kws = (kw if kw in ("*", "^") else f"~{kw}" for kw in sort_keywords(keywords))
            else:
                atom_str = pkg.versioned_atom
                kws = sort_keywords(keywords)
            yield f"{atom_str} {' '.join(kws)}"

    @property
    def dot_edge(self):
        if self.bugno is not None:
            return f"bug_{self.bugno}"
        return f'"{self.pkgs[0][0].versioned_atom}"'

    def cleanup_keywords(self, repo):
        previous = frozenset()
        for pkg, keywords in self.pkgs:
            if keywords == previous:
                keywords.clear()
                keywords.add("^")
            else:
                previous = frozenset(keywords)

        for pkg, keywords in self.pkgs:
            suggested = suggested_keywords(repo, pkg, stable=not self.is_keywordreq)
            if keywords == set(suggested):
                keywords.clear()
                keywords.add("*")

    @property
    def package_list(self) -> PackageList:
        return PackageList("\n".join(self.lines()))

    @property
    def bug_summary(self):
        return self.summary or summarise(self.package_list, self.category)

    @property
    def node_maintainers(self):
        return dict.fromkeys(
            maintainer.email for pkg, _ in self.pkgs for maintainer in pkg.maintainers
        )

    def should_cc_arches(self, auto_cc_arches: frozenset[str]):
        if self.cc_arches is not None:
            return self.cc_arches
        maintainers = self.node_maintainers
        return bool(
            not maintainers or "*" in auto_cc_arches or auto_cc_arches.intersection(maintainers)
        )

    def file_bug(
        self,
        bugzilla: Bugzilla,
        auto_cc_arches: frozenset[str],
        block_bugs: list[int],
        modified_repo: multiplex.tree,
        observer=None,
    ) -> int:
        if self.bugno is not None:
            # an already existing bug may still be missing deps, and may supersede older bugs
            if deps := self.file_missing_deps(bugzilla, auto_cc_arches, modified_repo, observer):
                with _naming(f"adding dependencies to bug {self.bugno} for {self}"):
                    bugzilla.update(self.bugno, BugUpdate(depends_on=ListChange.adding(*deps)))
            self.obsolete_bugs(bugzilla)
            return self.bugno
        self.file_missing_deps(bugzilla, auto_cc_arches, modified_repo, observer)

        description = [f"Please {self.category.verb}", ""]
        if modified_repo is not None:
            now = datetime.now(tz=UTC)
            for pkg, _ in self.pkgs:
                with contextlib.suppress(StopIteration):
                    match = next(modified_repo.itermatch(pkg.versioned_atom))
                    modified = datetime.fromtimestamp(match.time, tz=UTC)
                    days_old = (now - modified).days
                    description.append(
                        f" {pkg.versioned_atom.cpvstr}: no change for {days_old} days, since {modified:%Y-%m-%d}"
                    )

        with _naming(f"filing bug for {self}"):
            self.bugno = bugzilla.create(
                NewBug.arch_request(
                    self.category,
                    self.package_list,
                    maintainers=tuple(self.node_maintainers),
                    cc_arches=self.should_cc_arches(auto_cc_arches),
                    summary=self.bug_summary,
                    description="\n".join(description).strip(),
                    depends_on=tuple({dep.bugno for dep in self.edges}),
                    blocks=tuple(block_bugs),
                )
            )
        if observer is not None:
            observer(self)
        self.obsolete_bugs(bugzilla)
        return self.bugno

    def file_missing_deps(
        self,
        bugzilla: Bugzilla,
        auto_cc_arches: frozenset[str],
        modified_repo: multiplex.tree,
        observer=None,
    ) -> tuple[int, ...]:
        """File bugs for the dependencies which lack one, returning their bug numbers."""
        # collected upfront, as filing a dep may file another one sharing this node
        pending = tuple(dep for dep in self.edges if dep.bugno is None)
        for dep in pending:
            dep.file_bug(bugzilla, auto_cc_arches, (), modified_repo, observer)
        return tuple(dep.bugno for dep in pending)

    def obsolete_bugs(self, bugzilla: Bugzilla):
        if not self.obsoletes:
            return
        assert self.bugno is not None
        with _naming(f"obsoleting bugs by {self.bugno} for {self}"):
            bugzilla.update(sorted(self.obsoletes), BugUpdate.obsoleted_by(self.bugno))
        self.obsoletes.clear()  # don't repeat the update if visited again


class DependencyGraph:
    def __init__(self, out: Formatter, err: Formatter, options):
        self.out = out
        self.err = err
        self.options = options
        disabled, enabled = options.auto_cc_arches
        self.auto_cc_arches = frozenset(enabled).difference(disabled)
        self.profile_addon: ProfileAddon = init_addon(ProfileAddon, options)

        self.nodes: set[GraphNode] = set()
        self.starting_nodes: set[GraphNode] = set()
        self.targets: tuple[package] = ()
        self.target_arches: dict[package, frozenset[str]] = {}

        git_addon = init_addon(GitAddon, options)
        self.added_repo = git_addon.cached_repo(GitAddedRepo)
        self.modified_repo = git_addon.cached_repo(GitModifiedRepo)
        self.stablereq_check = stablereq.StableRequestCheck(self.options, git_addon=git_addon)
        self._stablereq_due: dict[str, tuple[str, ...]] = {}

    def mk_fake_pkg(self, pkg: package, keywords: set[str], stable: bool = True):
        kws = tuple(keywords) if stable else tuple(f"~{kw}" for kw in keywords)
        return MutatedPkg(pkg, {"keywords": kws})

    def stablereq_versions(self, unversioned: atom) -> tuple[str, ...]:
        """The versions of a package the stablereq check flags as due."""
        if (due := self._stablereq_due.get(unversioned.key)) is None:
            # the check reads every version, to tell which of them are stable
            pkgset = self.options.search_repo.match(unversioned)
            due = tuple(
                f"{res.category}/{res.package}-{res.version}"
                for res in self.stablereq_check.feed(sorted(pkgset))
                if isinstance(res, stablereq.StableRequest)
            )
            self._stablereq_due[unversioned.key] = due
        return due

    def find_best_match(self, restrict, pkgset: list[package], prefer_semi_stable=True) -> package:
        restrict = boolean.AndRestriction(
            *restrict,
            packages.PackageRestriction("properties", values.ContainmentMatch("live", negate=True)),
            packages.OrRestriction(*self.options.search_repo.pkg_masks, negate=True),
        )
        # prefer using user selected targets
        if intersect := tuple(filter(restrict.match, self.targets)):
            return max(intersect)
        # prefer using already selected packages in graph
        all_pkgs = (pkg for node in self.nodes for pkg, _ in node.pkgs)
        if intersect := tuple(filter(restrict.match, all_pkgs)):
            return max(intersect)
        matches = sorted(filter(restrict.match, pkgset), reverse=True)
        # prefer the version the stablereq check considers due for stabilization
        if self.options.filter_stablereqs and len(matches) > 1:
            by_cpv = {match.versioned_atom.cpvstr: match for match in matches}
            for cpvstr in self.stablereq_versions(matches[0].unversioned_atom):
                if match := by_cpv.get(cpvstr):
                    return match
        # prefer package with any stable keyword
        if prefer_semi_stable:
            for match in matches:
                if not all(keyword.startswith("~") for keyword in match.keywords):
                    return match
        # prefer package with any keyword
        for match in matches:
            if match.keywords:
                return match
        return matches[0]

    def extend_targets_stable_groups(self, groups):
        stabilization_groups = self.options.repo.stabilization_groups
        for group in groups:
            for pkg in stabilization_groups[group]:
                try:
                    yield None, pkg, frozenset()
                except (ValueError, IndexError):
                    self.err.write(f"Unable to find match for {pkg.unversioned_atom}")

    def _extend_projects(self, disabled: frozenset[str], enabled: frozenset[str]):
        members = defaultdict(set)
        self.out.write("Fetching projects.xml")
        self.out.flush()
        with urllib.urlopen("https://api.gentoo.org/metastructure/projects.xml", timeout=30) as f:
            for email, project in ProjectsXml(bytes_data_source(f.read())).projects.items():
                for member in project.members:
                    members[member.email].add(email)

        disabled = disabled.union(*(members[email] for email in disabled))
        enabled = enabled.union(*(members[email] for email in enabled))
        return disabled, enabled

    def extend_maintainers(self):
        disabled, enabled = self.options.find_by_maintainer
        disabled = frozenset({e if "@" in e else f"{e}@gentoo.org" for e in disabled})
        enabled = frozenset({e if "@" in e else f"{e}@gentoo.org" for e in enabled})
        if self.options.projects:
            disabled, enabled = self._extend_projects(disabled, enabled)
        emails = enabled.difference(disabled)
        if not emails:
            return
        search_repo = self.options.search_repo
        self.out.write("Searching for packages maintained by: ", ", ".join(emails))
        self.out.flush()
        for cat, pkgs in search_repo.packages.items():
            for pkg in pkgs:
                xml = LocalMetadataXml(pjoin(search_repo.location[0], cat, pkg, "metadata.xml"))
                if emails.intersection(m.email for m in xml.maintainers):
                    yield None, parserestrict.parse_match(f"{cat}/{pkg}"), frozenset()

    @staticmethod
    def _any_of_groups(pkg: package, attr: str) -> tuple[tuple[atom, ...], ...]:
        """The flat ``|| ( ... )`` blocks of one of ``pkg``'s depsets.

        Nested blocks are left out: an alternative spelled ``( a b )`` needs
        both of its atoms, which picking a single one can't express.
        """
        groups: list[tuple[atom, ...]] = []

        def walk(node: boolean.base):
            match node:
                case atom():
                    return
                case packages.Conditional():
                    children = node.payload
                case boolean.OrRestriction() if all(
                    isinstance(child, atom) for child in node.restrictions
                ):
                    groups.append(tuple(child.no_usedeps for child in node.restrictions))
                    return
                case _:
                    children = node.restrictions
            for child in children:
                walk(child)

        walk(getattr(pkg, attr))
        return tuple(groups)

    def _alternative_rank(self, dep: atom, keyword: str) -> tuple[bool, bool, bool]:
        """Rank any-of alternative by how small new work it needs.

        One with no version left to pick sorts last: it is masked, live or gone,
        and choosing it only turns into "unable to find match" further down.
        """
        matches = self.options.repo.match(dep)
        try:
            self.find_best_match({dep}, matches)
        except (ValueError, IndexError):
            return True, True, True
        selected = chain(self.targets, (p for node in self.nodes for p, _ in node.pkgs))
        keyworded = (
            keyword in match.keywords or f"~{keyword}" in match.keywords for match in matches
        )
        return False, not any(map(dep.match, selected)), not any(keyworded)

    def _pick_alternatives(
        self, groups: tuple[tuple[atom, ...], ...], keyword: str, deps: set[atom]
    ) -> set[atom]:
        """Reduce each failed any-of block in ``deps`` to a single alternative.

        An unsolvable ``|| ( ... )`` is reported as all of its atoms, since any
        one of them would solve it, but taking them all at face value files a
        bug per alternative and then walks the deps of packages nobody needs.
        Keep the one that asks for the least: already being handled, failing
        that already keyworded, failing that the ebuild's own first choice.
        """
        for group in groups:
            if len(alternatives := [dep for dep in group if dep in deps]) > 1:
                deps = deps.difference(alternatives)
                deps.add(min(alternatives, key=partial(self._alternative_rank, keyword=keyword)))
        return deps

    @staticmethod
    def _drop_settled(pkgset: list[package], arches: set[str], stable: bool) -> list[package]:
        """Prefer the versions ``arches`` still has to be asked for.

        A version already stable (or already keyworded) on an arch the check
        reported unsolvable can't be what the dependency needs there, filing for
        it again changes nothing. It gets picked all the same, being the closest
        match, when a use dep dropped with the rest of the atom is what makes a
        newer version the real answer. Nothing is left when the answer is a
        version that doesn't exist yet, so keep the full set for that error.
        """
        prefixes = ("",) if stable else ("", "~")
        settled = {f"{prefix}{arch}" for arch in arches for prefix in prefixes}
        return [pkg for pkg in pkgset if not settled.intersection(pkg.keywords)] or pkgset

    def _find_dependencies(self, pkg: package, keywords: set[str], stable: bool = True):
        check = visibility.VisibilityCheck(self.options, profile_addon=self.profile_addon)
        # the fake pkgs fed here aren't parsed ebuild sources (no .tree), so skip the
        # optfeature check, which requires a tree-sitter parse tree to run
        if hasattr(check, "check_optfeature"):
            check.check_optfeature = lambda pkg: iter(())

        # keyed by depset, as any-of blocks are only meaningful within one
        failures: dict[str, dict[str, set[atom]]] = defaultdict(partial(defaultdict, set))
        for res in check.feed(self.mk_fake_pkg(pkg, keywords, stable=stable)):
            if isinstance(res, visibility.NonsolvableDeps):
                deps = failures[res.attr][res.keyword.lstrip("~")]
                deps.update(atom(dep).no_usedeps for dep in res.deps)

        issues: dict[str, dict[str, set[atom]]] = defaultdict(partial(defaultdict, set))
        for attr, per_keyword in failures.items():
            groups = self._any_of_groups(pkg, attr)
            for keyword, deps in per_keyword.items():
                for dep in self._pick_alternatives(groups, keyword, deps):
                    issues[dep.key][keyword].add(dep)

        for pkgname, problems in issues.items():
            pkgset: list[package] = self.options.repo.match(atom(pkgname))
            # one version has to answer every failing arch, so exclude those settled
            # on any of them
            candidates = self._drop_settled(pkgset, set(problems), stable)
            try:
                match = self.find_best_match(set().union(*problems.values()), candidates)
                yield match, set(problems.keys())
            except (ValueError, IndexError):
                results: dict[package, set[str]] = defaultdict(set)
                for keyword, deps in problems.items():
                    candidates = self._drop_settled(pkgset, {keyword}, stable)
                    try:
                        match = self.find_best_match(deps, candidates)
                        results[match].add(keyword)
                    except (ValueError, IndexError):
                        # deps may contain contradictory version atoms (e.g. from
                        # multiple USE-conditional targets like net8.0/net9.0/net10.0),
                        # so try each atom individually
                        found = False
                        for dep in deps:
                            try:
                                match = self.find_best_match({dep}, candidates)
                                results[match].add(keyword)
                                found = True
                            except (ValueError, IndexError):
                                pass
                        if not found:
                            deps_str = " , ".join(map(str, deps))
                            bugs.error(
                                f"unable to find match for restrictions: {deps_str}",
                                status=3,
                            )
                yield from results.items()

    def _stablereqs(self, pkgset) -> list[tuple[atom, list[package]]]:
        """The stablereq of every package in the set, as a target of its own."""
        per_pkg: dict[str, list[package]] = defaultdict(list)
        for pkg in pkgset:
            per_pkg[pkg.key].append(pkg)

        found = []
        # the check reads the versions of a single package, so feed one at a time
        for pkgs in per_pkg.values():
            for res in self.stablereq_check.feed(sorted(pkgs)):
                if isinstance(res, stablereq.StableRequest):
                    found.append((atom(f"={res.category}/{res.package}-{res.version}"), pkgs))
                    break
        return found

    def load_targets(self, targets: list[tuple[str, object, frozenset[str]]]):
        result = []
        search_repo = self.options.search_repo
        masked = packages.OrRestriction(*self.options.search_repo.pkg_masks)
        for _, target, arches in targets:
            try:
                pkgset = search_repo.match(target)
                if self.options.filter_stablereqs:
                    # a target may match many packages, each with its own stablereq
                    found = self._stablereqs(pkgset)
                else:
                    found = [(target, pkgset)]

                for restrict, candidates in found:
                    if masked.match(restrict):
                        self.err.write(
                            self.err.fg("yellow"),
                            f"Target {restrict} is masked, skipping",
                            self.err.reset,
                        )
                        continue
                    match = self.find_best_match([restrict], candidates, False)
                    result.append(match)
                    if arches:
                        self.target_arches[match] = arches
            except (ValueError, IndexError):
                bugs.error(f"Restriction {target} has no match in repository", status=3)
        self.targets = tuple(result)

    def _reject_masked_keywords(self, pkg: package, arches: set[str], reason: str):
        all_masked = "-*" in pkg.keywords
        masked = sorted(
            a
            for a in arches
            if f"-{a}" in pkg.keywords
            or (all_masked and a not in pkg.keywords and f"~{a}" not in pkg.keywords)
        )
        if masked:
            origin = f" (required by {reason})" if reason else ""
            via = "-*" if all_masked else ", ".join("-" + a for a in masked)
            bugs.error(
                f"{pkg.versioned_atom} masks keyword(s) {', '.join(masked)} via "
                f"{via}{origin}; refusing to file a keywording request, the mask must "
                f"be removed manually first",
                status=3,
            )

    def build_full_graph(self):
        STABLEREQ, KEYWORDREQ = BugCategory.STABLEREQ, BugCategory.KEYWORDREQ
        check_nodes = [
            (pkg, set(self.target_arches.get(pkg, ())), self.options.category, "")
            for pkg in self.targets
        ]

        vertices: dict[tuple[package, BugCategory], GraphNode] = {}
        edges = []

        def explore_deps(pkg: package, arches: set[str], category: BugCategory):
            """Queue the dependencies of ``pkg`` that are unsolvable on ``arches``."""
            for dep, dep_arches in self._find_dependencies(
                pkg, arches, stable=category is STABLEREQ
            ):
                if category is STABLEREQ:
                    # the dep must become stable on dep_arches
                    edges.append(((pkg, STABLEREQ), (dep, STABLEREQ)))
                    check_nodes.append((dep, set(dep_arches), STABLEREQ, str(pkg.versioned_atom)))
                    # arches the dep isn't keyworded on at all must be keyworded first;
                    # chain dep-stablereq -> dep-keywordreq
                    keyword_needed = {
                        a
                        for a in dep_arches
                        if a not in dep.keywords and f"~{a}" not in dep.keywords
                    }
                    if keyword_needed:
                        edges.append(((dep, STABLEREQ), (dep, KEYWORDREQ)))
                        check_nodes.append(
                            (dep, set(keyword_needed), KEYWORDREQ, str(pkg.versioned_atom))
                        )
                else:
                    edges.append(((pkg, KEYWORDREQ), (dep, KEYWORDREQ)))
                    check_nodes.append((dep, set(dep_arches), KEYWORDREQ, str(pkg.versioned_atom)))

        while len(check_nodes):
            pkg, keywords, category, reason = check_nodes.pop(0)
            if (pkg, category) in vertices:
                # already visited: add any genuinely new arches and explore their deps
                existing = vertices[(pkg, category)].pkgs[0][1]
                if new_arches := keywords - existing:
                    if category is KEYWORDREQ:
                        self._reject_masked_keywords(pkg, new_arches, reason)
                    existing.update(new_arches)
                    explore_deps(pkg, new_arches, category)
                continue

            streq = category is STABLEREQ
            verb = category.verb
            if streq:
                keywords.update(suggested_keywords(self.options.repo, pkg, stable=True))
                if not keywords:
                    # nothing left to stabilize (already stable or never keyworded)
                    self.out.write(f"Nothing to stable for {pkg.unversioned_atom}")
                    continue
            else:
                # explicit (command line) or dependency-driven arches are authoritative;
                # only fall back to the other-versions heuristic when none were given
                if not keywords:
                    keywords.update(suggested_keywords(self.options.repo, pkg, stable=False))
                if not keywords:
                    # keywordreq with no derivable arches: the user must specify them
                    bugs.error(
                        f"no keywords to add for {pkg.versioned_atom}; specify arches "
                        f"explicitly on the command line, e.g. '{pkg.unversioned_atom} <arch>...'",
                        status=3,
                    )
                self._reject_masked_keywords(pkg, keywords, reason)
            self.nodes.add(new_node := GraphNode(((pkg, keywords),), category=category))
            vertices[(pkg, category)] = new_node
            if reason:
                reason = f" [added for {reason}]"
            self.out.write(
                f"Checking {pkg.versioned_atom} to {verb} on "
                f"{' '.join(sort_keywords(keywords))!r}{reason}"
            )
            self.out.flush()

            explore_deps(pkg, keywords, category)

        for src, dst in edges:
            if (src_node := vertices.get(src)) is not None and (
                dst_node := vertices.get(dst)
            ) is not None:
                src_node.edges.add(dst_node)
        self.starting_nodes = {
            vertices[(starting_node, self.options.category)]
            for starting_node in self.targets
            if (starting_node, self.options.category) in vertices
        }

    def output_dot(self, dot_file: str):
        with open(dot_file, "w") as dot:
            dot.write("digraph {\n")
            dot.write("\trankdir=LR;\n")
            for node in self.nodes:
                node_text = "\\n".join(node.lines())
                if node.bugno is not None:
                    node_text += f"\\nbug #{node.bugno}"
                dot.write(f'\t{node.dot_edge}[label="{node_text}"];\n')
                dot.writelines(f"\t{node.dot_edge} -> {other.dot_edge};\n" for other in node.edges)
            dot.write("}\n")
            dot.close()

    def output_graph_toml(self):
        bugs = dict(enumerate(self.nodes, start=1))
        reverse_bugs = {node: bugno for bugno, node in bugs.items()}

        toml = tempfile.NamedTemporaryFile(mode="w", suffix=".toml")
        for bugno, node in bugs.items():
            if node.bugno is not None:
                continue  # already filed
            toml.write(f"[bug-{bugno}]\n")
            toml.write(f'summary = "{node.bug_summary}"\n')
            toml.write(f'category = "{node.category.summary_suffix}"\n')
            toml.write(f"cc_arches = {str(node.should_cc_arches(self.auto_cc_arches)).lower()}\n")
            if node in self.starting_nodes:
                toml.write("starting = true\n")
            if node_depends := ", ".join(
                (f'"bug-{reverse_bugs[dep]}"' if dep.bugno is None else str(dep.bugno))
                for dep in node.edges
            ):
                toml.write(f"depends = [{node_depends}]\n")
            if node_blocks := ", ".join(
                f'"bug-{i}"' for i, src in bugs.items() if node in src.edges
            ):
                toml.write(f"blocks = [{node_blocks}]\n")
            toml.write(f"obsoletes = {sorted(node.obsoletes)}\n")
            now = datetime.now(tz=UTC)
            for pkg, arches in node.pkgs:
                try:
                    match = next(self.modified_repo.itermatch(pkg.versioned_atom))
                    modified = datetime.fromtimestamp(match.time, tz=UTC)
                    age = (now - modified).days
                    modified_text = f"{modified:%Y-%m-%d} (age {age} days)"
                except StopIteration:
                    modified_text = "<unknown>"

                try:
                    match = next(self.added_repo.itermatch(pkg.versioned_atom))
                    added = datetime.fromtimestamp(match.time, tz=UTC)
                    age = (now - added).days
                    added_text = f"{added:%Y-%m-%d} (age {age} days)"
                except StopIteration:
                    added_text = "<unknown>"

                toml.write(f"# added on {added_text}, last modified on {modified_text}\n")
                keywords = ", ".join(f'"{x}"' for x in sort_keywords(arches))
                toml.write(f'"{pkg.versioned_atom}" = [{keywords}]\n')
            toml.write("\n\n")
        toml.flush()
        return toml

    def load_graph_toml(self, toml_file: str):
        repo = self.options.search_repo
        with open(toml_file, "rb") as f:
            data = tomllib.load(f)

        new_bugs: dict[int | str, GraphNode] = {}
        for node_name, data_node in data.items():
            pkgs = tuple(
                (next(repo.itermatch(atom(pkg))), set(keywords))
                for pkg, keywords in data_node.items()
                if pkg.startswith("=")
            )
            category = _CATEGORY_BY_SUFFIX.get(
                data_node.get("category", "stablereq"), BugCategory.STABLEREQ
            )
            new_bugs[node_name] = GraphNode(pkgs, category=category)
        for node_name, data_node in data.items():
            new_bugs[node_name].summary = data_node.get("summary", "")
            new_bugs[node_name].cc_arches = data_node.get("cc_arches", None)
            new_bugs[node_name].obsoletes = set(data_node.get("obsoletes", ()))
            for dep in data_node.get("depends", ()):
                if isinstance(dep, int):
                    new_bugs[node_name].edges.add(
                        new_bugs.setdefault(dep, GraphNode((), bugno=dep))
                    )
                elif new_bugs.get(dep) is not None:
                    new_bugs[node_name].edges.add(new_bugs[dep])
                else:
                    raise ValueError(f"[{node_name}]['depends']: unknown dependency {dep!r}")
        self.nodes = set(new_bugs.values())
        self.starting_nodes = {
            new_bugs[node_name]
            for node_name, data_node in data.items()
            if data_node.get("starting", False)
        }

    def merge_nodes(self, nodes: tuple[GraphNode, ...]) -> GraphNode:
        categories = {node.category for node in nodes}
        assert len(categories) == 1, f"refusing to merge nodes of mixed categories: {categories}"
        bugnos = {node.bugno for node in nodes if node.bugno is not None}
        if len(bugnos) > 1:
            bugs.error(
                "cannot merge nodes matched to different existing bugs: "
                + ", ".join(f"https://bugs.gentoo.org/{bugno}" for bugno in sorted(bugnos)),
                status=3,
            )
        self.nodes.difference_update(nodes)
        is_start = bool(self.starting_nodes.intersection(nodes))
        self.starting_nodes.difference_update(nodes)
        new_node = GraphNode(
            list(chain.from_iterable(n.pkgs for n in nodes)),
            category=categories.pop(),
            bugno=next(iter(bugnos), None),
        )

        for node in nodes:
            new_node.edges.update(node.edges.difference(nodes))
            new_node.obsoletes.update(node.obsoletes)  # inherit pending obsoletions
        new_node.obsoletes.discard(new_node.bugno)  # never obsolete our own bug

        for node in self.nodes:
            if node.edges.intersection(nodes):
                node.edges.difference_update(nodes)
                node.edges.add(new_node)

        self.nodes.add(new_node)
        if is_start:
            self.starting_nodes.add(new_node)
        return new_node

    def merge_matched_bugs(self):
        """Merge the nodes which matched the same existing bug."""
        shared: dict[int, list[GraphNode]] = defaultdict(list)
        for node in self.nodes:
            if node.bugno is not None:
                shared[node.bugno].append(node)

        for bugno, nodes in sorted(shared.items()):
            if len(nodes) > 1:
                self.out.write(
                    f"Merging {len(nodes)} nodes matched to bug {bugno}: ",
                    ", ".join(map(str, nodes)),
                )
                self.merge_nodes(tuple(nodes))

        # a bug still in use can't also be resolved as obsolete
        in_use = {node.bugno for node in self.nodes if node.bugno is not None}
        for node in self.nodes:
            for bugno in sorted(node.obsoletes.intersection(in_use)):
                self.out.warn(
                    f"not obsoleting bug {bugno}, it is the bug of another node in the graph"
                )
            node.obsoletes.difference_update(in_use)

    @staticmethod
    def _find_cycles(nodes: tuple[GraphNode, ...], stack: list[GraphNode]) -> tuple[GraphNode, ...]:
        node = stack[-1]
        for edge in node.edges:
            if edge in stack:
                return tuple(stack[stack.index(edge) :])
            stack.append(edge)
            if cycle := DependencyGraph._find_cycles(nodes, stack):
                return cycle
            stack.pop()
        return ()

    def merge_cycles(self):
        start_nodes = set(self.starting_nodes)
        while start_nodes:
            starting_node = start_nodes.pop()
            assert starting_node in self.nodes
            while cycle := self._find_cycles(tuple(self.nodes), [starting_node]):
                self.out.write("Found cycle: ", " -> ".join(str(n) for n in cycle))
                if len({node.category for node in cycle}) != 1:
                    bugs.error(
                        "found a dependency cycle spanning both keywording and "
                        f"stabilization, which cannot be merged: {' -> '.join(map(str, cycle))}",
                        status=3,
                    )
                start_nodes.difference_update(cycle)
                new_node = self.merge_nodes(cycle)
                if starting_node not in self.nodes:
                    starting_node = new_node

    def merge_new_keywords_children(self):
        repo = self.options.search_repo
        found_someone = True
        while found_someone:
            reverse_edges: dict[GraphNode, set[GraphNode]] = defaultdict(set)
            for node in self.nodes:
                for dep in node.edges:
                    reverse_edges[dep].add(node)
            found_someone = False
            for node, origs in reverse_edges.items():
                if len(origs) != 1:
                    continue
                if node.bugno is not None:
                    continue
                existing_keywords = frozenset().union(
                    *(
                        pkgver.keywords
                        for pkg, _ in node.pkgs
                        for pkgver in repo.match(pkg.unversioned_atom)
                    )
                )
                if existing_keywords & frozenset().union(*(pkg[1] for pkg in node.pkgs)):
                    continue  # not fully new keywords
                orig = next(iter(origs))
                if orig.bugno is not None:
                    continue
                if orig.category is not node.category:
                    # never fold a keywordreq companion into its stablereq parent: that
                    # would put ~arch keywords into a Stabilization bug
                    continue
                self.out.write(f"Merging {node} into {orig}")
                self.merge_nodes((orig, node))
                found_someone = True
                break

    def merge_stabilization_groups(self, out: Formatter, err: Formatter) -> bool:
        all_pkgs = {pkg.unversioned_atom for node in self.nodes for pkg, _ in node.pkgs}
        for group, pkgs in self.options.repo.stabilization_groups.items():
            restrict = packages.OrRestriction(*pkgs)
            mergable = tuple(
                node
                for node in self.nodes
                if node.bugno is None
                and node.category is BugCategory.STABLEREQ
                and any(restrict.match(pkg) for pkg, _ in node.pkgs)
            )
            if mergable:
                if missing_pkgs := pkgs - all_pkgs:
                    self.out.write(
                        self.out.fg("yellow"),
                        f"Detected {len(missing_pkgs)} missing packages in @{group} group\n",
                        "\n".join(f" - {pkg}" for pkg in sorted(missing_pkgs)),
                        self.out.reset,
                    )
                    if not userquery(
                        " Confirm this was intentional?", out, err, default_answer=False
                    ):
                        return False
                self.out.write(f"Merging @{group} group nodes: {mergable}")
                self.merge_nodes(mergable)
        return True

    def scan_existing_bugs(self, bugzilla: Bugzilla) -> bool:
        all_packages = list({pkg[0].unversioned_atom for node in self.nodes for pkg in node.pkgs})
        has_output = False

        query = (
            BugQuery.component(BugCategory.KEYWORDREQ, BugCategory.STABLEREQ)
            & BugQuery.unresolved()
            & BugQuery.package_list_any(all_packages)
        )
        all_bugs = bugzilla.search(query).values()

        matches: dict[GraphNode, list[tuple[bool, Bug]]] = defaultdict(list)
        for bug in all_bugs:
            bug_atoms = bug.package_list.atoms
            bug_match = boolean.OrRestriction(*(a.unversioned_atom for a in bug_atoms))
            exact_match = boolean.OrRestriction(*bug_atoms)
            for node in self.nodes:
                if bug.component != node.category.component:
                    continue
                if node.bugno is None and all(bug_match.match(pkg[0]) for pkg in node.pkgs):
                    is_exact_match = all(exact_match.match(pkg[0]) for pkg in node.pkgs)
                    matches[node].append((is_exact_match, bug))

        for node, node_bugs in matches.items():
            # exact matches first, so the node keeps the exact bug and obsoletes
            # the atom matches, regardless of the order bugzilla returned them in
            for is_exact_match, bug in sorted(node_bugs, key=lambda m: (not m[0], m[1].id)):
                self.out.write(
                    self.out.fg("yellow"),
                    f"Found {bug.url} for node {node}",
                    self.out.reset,
                    " (exact version match)" if is_exact_match else " (atom match)",
                )
                self.out.write(" -> bug summary: ", bug.summary)
                if is_exact_match and node.bugno is None:
                    node.bugno = bug.id
                elif userquery(
                    f"{'Duplicate of the matched bug' if is_exact_match else 'Not an exact match'}."
                    " Do you want to obsolete?",
                    self.out,
                    self.err,
                    default_answer=False,
                ):
                    node.obsoletes.add(bug.id)
                elif node.bugno is None:
                    node.bugno = bug.id
                has_output = True
        return has_output

    def file_bugs(self, bugzilla: Bugzilla, auto_cc_arches: frozenset[str], block_bugs: list[int]):
        def observe(node: GraphNode):
            self.out.write(
                f"https://bugs.gentoo.org/{node.bugno} ",
                " | ".join(node.lines()),
                " depends on bugs ",
                {dep.bugno for dep in node.edges} or "{}",
            )
            self.out.flush()

        for node in self.starting_nodes:
            node.file_bug(bugzilla, auto_cc_arches, block_bugs, self.modified_repo, observe)
        self.obsolete_bugs(bugzilla)

    def obsolete_bugs(self, bugzilla: Bugzilla):
        # nodes not walked by file_bugs may still have obsoletions pending
        for node in self.nodes:
            if node.bugno is not None:
                node.obsolete_bugs(bugzilla)


def _load_from_stdin(out: Formatter):
    if not sys.stdin.isatty():
        out.warn("No packages were specified, reading from stdin...")
        for line in sys.stdin.readlines():
            if line := line.split("#", 1)[0].strip():
                atom_str, *arches = line.split()
                yield atom_str, parserestrict.parse_match(atom_str), frozenset(arches)
        # reassign stdin to allow interactivity (currently only works for unix)
        sys.stdin = open("/dev/tty")  # noqa: SIM115
    else:
        bugs.error("reading from stdin is only valid when piping data in")


@bugs.bind_main_func
def main(options, out: Formatter, err: Formatter):
    if options.api_key is None:
        err.write(out.fg("red"), "No API key provided, exiting", out.reset)
        return 1

    search_repo = options.search_repo
    options.targets = options.targets or []
    d = DependencyGraph(out, err, options)
    options.targets.extend(d.extend_maintainers())
    options.targets.extend(d.extend_targets_stable_groups(options.sets or ()))
    if not options.targets:
        options.targets = list(_load_from_stdin(out))
    d.load_targets(options.targets)
    d.build_full_graph()

    if not d.nodes:
        out.write(out.fg("red"), "Nothing to do, exiting", out.reset)
        return 1

    has_output = False
    if userquery("Check for open bugs matching current graph?", out, err, default_answer=False):
        if d.scan_existing_bugs(options.bugzilla):
            out.flush()
            has_output = True

    d.merge_matched_bugs()
    if not d.merge_stabilization_groups(out, err):
        out.write(out.fg("red"), "Aborted", out.reset)
        return 1
    d.merge_cycles()
    d.merge_new_keywords_children()

    if options.edit_graph:
        toml = d.output_graph_toml()

    for node in d.nodes:
        node.cleanup_keywords(search_repo)

    if options.dot is not None:
        d.output_dot(options.dot)
        out.write(out.fg("green"), f"Dot file written to {options.dot}", out.reset)
        out.flush()
        has_output = True

    if options.edit_graph:
        if has_output and not userquery("Ready to open editor?", out, err, default_answer=True):
            out.write(out.fg("red"), "Aborted", out.reset)
            return 1

        editor = shlex.split(os.environ.get("VISUAL", os.environ.get("EDITOR", "nano")))
        while True:
            try:
                subprocess.run(editor + [toml.name], check=True)
            except subprocess.CalledProcessError:
                bugs.error("failed opening editor, aborting")
            except FileNotFoundError:
                bugs.error(f"nonexistent editor: {editor[0]!r}")
            try:
                d.load_graph_toml(toml.name)
            except Exception as e:
                err.write(err.fg("red"), f"Invalid graph: {e}", err.reset)
                err.flush()
                if userquery("  Reopen editor to fix the error?", out, err, default_answer=True):
                    continue
                return 1
            break
        for node in d.nodes:
            node.cleanup_keywords(search_repo)

        if options.dot is not None:
            d.output_dot(options.dot)
            out.write(out.fg("green"), f"Dot file written to {options.dot}", out.reset)
            out.flush()

    pending = [node for node in d.nodes if node.bugno is None]
    if not pending:
        # no bugs to file, but obsoletions of matched bugs may still be pending
        if (obsoletes := {bugno for node in d.nodes for bugno in node.obsoletes}) and userquery(
            f"No bugs to file, still obsolete {len(obsoletes)} bugs?",
            out,
            err,
            default_answer=False,
        ):
            d.obsolete_bugs(options.bugzilla)
            return 0
        out.write(out.fg("red"), "Nothing to do, exiting", out.reset)
        return 1
    counts = {
        category.summary_suffix: sum(node.category is category for node in pending)
        for category in BugCategory
    }
    summary = ", ".join(f"{count} {suffix}" for suffix, count in counts.items() if count)

    if not userquery(
        f"Continue and create {len(pending)} bugs ({summary})?", out, err, default_answer=False
    ):
        return 1

    disabled, enabled = options.auto_cc_arches
    blocks = list(frozenset(map(int, options.blocks)))
    d.file_bugs(options.bugzilla, frozenset(enabled).difference(disabled), blocks)
