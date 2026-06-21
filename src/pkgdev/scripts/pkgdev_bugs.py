"""Automatic bugs filer"""

import contextlib
import enum
import json
import os
import shlex
import subprocess
import sys
import tempfile
import tomllib
import urllib.request as urllib
from collections import defaultdict
from datetime import datetime
from functools import partial
from itertools import chain
from os.path import join as pjoin
from urllib.parse import urlencode

from pkgcheck import const as pkgcheck_const
from pkgcheck.addons import ArchesAddon, init_addon
from pkgcheck.addons.git import GitAddedRepo, GitAddon, GitModifiedRepo
from pkgcheck.addons.profiles import ProfileAddon
from pkgcheck.checks import stablereq, visibility
from pkgcheck.scripts import argparse_actions
from pkgcore.ebuild.atom import atom
from pkgcore.ebuild.ebuild_src import package
from pkgcore.ebuild.errors import MalformedAtom
from pkgcore.ebuild.misc import sort_keywords
from pkgcore.ebuild.repo_objs import LocalMetadataXml, ProjectsXml
from pkgcore.repository import multiplex
from pkgcore.restrictions import boolean, packages, values
from pkgcore.test.misc import FakePkg
from pkgcore.util import commandline, parserestrict
from snakeoil.cli import arghparse
from snakeoil.cli.input import userquery
from snakeoil.data_source import bytes_data_source
from snakeoil.formatters import Formatter

from ..cli import ArgumentParser
from .argparsers import BugzillaApiKey, _determine_cwd_repo, cwd_repo_argparser


class NodeCategory(enum.Enum):
    KEYWORDREQ = enum.auto()
    STABLEREQ = enum.auto()


# per-category strings: Bugzilla component, description verb, summary suffix
_CATEGORY_META = {
    NodeCategory.STABLEREQ: {
        "component": "Stabilization",
        "verb": "stabilize",
        "suffix": "stablereq",
    },
    NodeCategory.KEYWORDREQ: {
        "component": "Keywording",
        "verb": "keyword",
        "suffix": "keywordreq",
    },
}

_CATEGORY_BY_SUFFIX = {meta["suffix"]: category for category, meta in _CATEGORY_META.items()}


class StoreTargetArches(commandline.StoreTarget):
    """``StoreTarget`` variant accepting trailing arches after each atom.

    A target may carry a whitespace separated list of arches after the atom,
    nattka-style, e.g. ``=cat/pkg-1.0 amd64 x86``. This produces 3-tuples
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
                sys.stdin = open("/dev/tty")
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
    setattr(namespace, "verbosity", 1)
    setattr(namespace, "search_repo", search_repo := multiplex.tree(*namespace.repo.trees))
    setattr(namespace, "gentoo_repo", search_repo)
    setattr(namespace, "query_caching_freq", "package")


@bugs.bind_final_check
def _validate_args(parser, namespace):
    if namespace.keywording and namespace.filter_stablereqs:
        parser.error("--keywording is incompatible with --filter-stablereqs")
    namespace.category = NodeCategory.KEYWORDREQ if namespace.keywording else NodeCategory.STABLEREQ


def _get_suggested_keywords(repo, pkg: package, streq: bool = True):
    # for stablereq only consider already stable keywords on other versions, for
    # keywordreq also consider ~arch keywords (those can be propagated as new keywords)
    disallow_prefix = "-~" if streq else "-"
    match_keywords = {
        x.lstrip("~")
        for pkgver in repo.match(pkg.unversioned_atom)
        for x in pkgver.keywords
        if x[0] not in disallow_prefix
    }

    if streq:
        # limit stablereq to whatever is ~arch right now
        match_keywords.intersection_update(x.lstrip("~") for x in pkg.keywords if x[0] == "~")
    else:
        # limit keywordreq to missing keywords (strip all keywords already present)
        match_keywords.difference_update(x.lstrip("~-") for x in pkg.keywords)

    return frozenset({x for x in match_keywords if "-" not in x})


def parse_atom(pkg: str):
    try:
        return atom(pkg)
    except MalformedAtom as exc:
        try:
            return atom(f"={pkg}")
        except MalformedAtom:
            raise exc


class GraphNode:
    __slots__ = ("pkgs", "category", "edges", "bugno", "summary", "cc_arches", "obsoletes")

    def __init__(
        self,
        pkgs: tuple[tuple[package, set[str]], ...],
        category: NodeCategory = NodeCategory.STABLEREQ,
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
        return self.category is NodeCategory.KEYWORDREQ

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
            suggested = _get_suggested_keywords(repo, pkg, streq=not self.is_keywordreq)
            if keywords == set(suggested):
                keywords.clear()
                keywords.add("*")

    @property
    def bug_summary(self):
        if self.summary:
            return self.summary
        suffix = _CATEGORY_META[self.category]["suffix"]
        if self.is_keywordreq:
            names = [str(pkg.unversioned_atom) for pkg, _ in self.pkgs]
        else:
            names = [pkg.versioned_atom.cpvstr for pkg, _ in self.pkgs]
        summary = f"{', '.join(names)}: {suffix}"
        if len(summary) > 90 and len(self.pkgs) > 1:
            return f"{names[0]} and friends: {suffix}"
        return summary

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
        api_key: str,
        auto_cc_arches: frozenset[str],
        block_bugs: list[int],
        modified_repo: multiplex.tree,
        observer=None,
    ) -> int:
        if self.bugno is not None:
            return self.bugno
        for dep in self.edges:
            if dep.bugno is None:
                dep.file_bug(api_key, auto_cc_arches, (), modified_repo, observer)
        maintainers = self.node_maintainers
        if self.should_cc_arches(auto_cc_arches):
            keywords = ["CC-ARCHES"]
        else:
            keywords = []
        maintainers = tuple(maintainers) or ("maintainer-needed@gentoo.org",)

        description = [f"Please {_CATEGORY_META[self.category]['verb']}", ""]
        if modified_repo is not None:
            for pkg, _ in self.pkgs:
                with contextlib.suppress(StopIteration):
                    match = next(modified_repo.itermatch(pkg.versioned_atom))
                    modified = datetime.fromtimestamp(match.time)
                    days_old = (datetime.today() - modified).days
                    description.append(
                        f" {pkg.versioned_atom.cpvstr}: no change for {days_old} days, since {modified:%Y-%m-%d}"
                    )

        request_data = dict(
            Bugzilla_api_key=api_key,
            product="Gentoo Linux",
            component=_CATEGORY_META[self.category]["component"],
            severity="enhancement",
            version="unspecified",
            summary=self.bug_summary,
            description="\n".join(description).strip(),
            keywords=keywords,
            cf_stabilisation_atoms="\n".join(self.lines()),
            assigned_to=maintainers[0],
            cc=maintainers[1:],
            depends_on=list({dep.bugno for dep in self.edges}),
            blocks=block_bugs,
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
        self.bugno = int(reply["id"])
        if observer is not None:
            observer(self)
        self.obsolete_bugs(api_key)
        return self.bugno

    def obsolete_bugs(self, api_key: str):
        if not self.obsoletes:
            return
        assert self.bugno is not None

        # Batch all bug IDs into a single PUT request
        request_data = dict(
            Bugzilla_api_key=api_key,
            status="RESOLVED",
            resolution="OBSOLETE",
            see_also={"add": [f"https://bugs.gentoo.org/{self.bugno}"]},
        )
        if len(self.obsoletes) > 1:
            request_data["ids"] = list(self.obsoletes)
        request = urllib.Request(
            url=f"https://bugs.gentoo.org/rest/bug/{','.join(map(str, self.obsoletes))}",
            data=json.dumps(request_data).encode("utf-8"),
            method="PUT",
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )
        with urllib.urlopen(request, timeout=30) as response:
            json.loads(response.read().decode("utf-8"))


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

    def mk_fake_pkg(self, pkg: package, keywords: set[str], stable: bool = True):
        kws = tuple(keywords) if stable else tuple(f"~{kw}" for kw in keywords)
        return FakePkg(
            cpv=pkg.cpvstr,
            eapi=str(pkg.eapi),
            iuse=pkg.iuse,
            repo=pkg.repo,
            keywords=kws,
            data={attr: str(getattr(pkg, attr.lower())) for attr in pkg.eapi.dep_keys},
        )

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

    def _find_dependencies(self, pkg: package, keywords: set[str], stable: bool = True):
        check = visibility.VisibilityCheck(self.options, profile_addon=self.profile_addon)

        issues: dict[str, dict[str, set[atom]]] = defaultdict(partial(defaultdict, set))
        for res in check.feed(self.mk_fake_pkg(pkg, keywords, stable=stable)):
            if isinstance(res, visibility.NonsolvableDeps):
                for dep in res.deps:
                    dep: atom = atom(dep).no_usedeps
                    issues[dep.key][res.keyword.lstrip("~")].add(dep)

        for pkgname, problems in issues.items():
            pkgset: list[package] = self.options.repo.match(atom(pkgname))
            try:
                match = self.find_best_match(set().union(*problems.values()), pkgset)
                yield match, set(problems.keys())
            except (ValueError, IndexError):
                results: dict[package, set[str]] = defaultdict(set)
                for keyword, deps in problems.items():
                    try:
                        match = self.find_best_match(deps, pkgset)
                        results[match].add(keyword)
                    except (ValueError, IndexError):
                        # deps may contain contradictory version atoms (e.g. from
                        # multiple USE-conditional targets like net8.0/net9.0/net10.0),
                        # so try each atom individually
                        found = False
                        for dep in deps:
                            try:
                                match = self.find_best_match({dep}, pkgset)
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

    def load_targets(self, targets: list[tuple[str, object, frozenset[str]]]):
        result = []
        search_repo = self.options.search_repo
        masked = packages.OrRestriction(*self.options.search_repo.pkg_masks)
        for _, target, arches in targets:
            try:
                pkgset = search_repo.match(target)
                if self.options.filter_stablereqs:
                    for res in self.stablereq_check.feed(sorted(pkgset)):
                        if isinstance(res, stablereq.StableRequest):
                            target = atom(f"={res.category}/{res.package}-{res.version}")
                            break
                    else:  # no stablereq
                        continue
                if masked.match(target):
                    self.err.write(
                        self.err.fg("yellow"),
                        f"Target {target} is masked, skipping",
                        self.err.reset,
                    )
                    continue
                match = self.find_best_match([target], pkgset, False)
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
        STABLEREQ, KEYWORDREQ = NodeCategory.STABLEREQ, NodeCategory.KEYWORDREQ
        check_nodes = [
            (pkg, set(self.target_arches.get(pkg, ())), self.options.category, "")
            for pkg in self.targets
        ]

        vertices: dict[tuple[package, NodeCategory], GraphNode] = {}
        edges = []

        def explore_deps(pkg: package, arches: set[str], category: NodeCategory):
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
            verb = _CATEGORY_META[category]["verb"]
            if streq:
                keywords.update(_get_suggested_keywords(self.options.repo, pkg, streq=True))
                if not keywords:
                    # nothing left to stabilize (already stable or never keyworded)
                    self.out.write(f"Nothing to stable for {pkg.unversioned_atom}")
                    continue
            else:
                # explicit (command line) or dependency-driven arches are authoritative;
                # only fall back to the other-versions heuristic when none were given
                if not keywords:
                    keywords.update(_get_suggested_keywords(self.options.repo, pkg, streq=False))
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
                for other in node.edges:
                    dot.write(f"\t{node.dot_edge} -> {other.dot_edge};\n")
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
            toml.write(f'category = "{_CATEGORY_META[node.category]["suffix"]}"\n')
            toml.write(f"cc_arches = {str(node.should_cc_arches(self.auto_cc_arches)).lower()}\n")
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
            for pkg, arches in node.pkgs:
                try:
                    match = next(self.modified_repo.itermatch(pkg.versioned_atom))
                    modified = datetime.fromtimestamp(match.time)
                    age = (datetime.today() - modified).days
                    modified_text = f"{modified:%Y-%m-%d} (age {age} days)"
                except StopIteration:
                    modified_text = "<unknown>"

                try:
                    match = next(self.added_repo.itermatch(pkg.versioned_atom))
                    added = datetime.fromtimestamp(match.time)
                    age = (datetime.today() - added).days
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
                data_node.get("category", "stablereq"), NodeCategory.STABLEREQ
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
        self.starting_nodes = {node for node in self.nodes if not node.edges}

    def merge_nodes(self, nodes: tuple[GraphNode, ...]) -> GraphNode:
        categories = {node.category for node in nodes}
        assert len(categories) == 1, f"refusing to merge nodes of mixed categories: {categories}"
        self.nodes.difference_update(nodes)
        is_start = bool(self.starting_nodes.intersection(nodes))
        self.starting_nodes.difference_update(nodes)
        new_node = GraphNode(
            list(chain.from_iterable(n.pkgs for n in nodes)), category=categories.pop()
        )

        for node in nodes:
            new_node.edges.update(node.edges.difference(nodes))

        for node in self.nodes:
            if node.edges.intersection(nodes):
                node.edges.difference_update(nodes)
                node.edges.add(new_node)

        self.nodes.add(new_node)
        if is_start:
            self.starting_nodes.add(new_node)
        return new_node

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
                and node.category is NodeCategory.STABLEREQ
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

    def scan_existing_bugs(self, api_key: str) -> bool:
        # Paginate the search request with batches of 100 items to avoid HTTP 414 errors
        all_packages = list({pkg[0].unversioned_atom for node in self.nodes for pkg in node.pkgs})
        batch_size = 100
        all_bugs = []
        has_output = False

        for i in range(0, len(all_packages), batch_size):
            params = urlencode(
                {
                    "Bugzilla_api_key": api_key,
                    "include_fields": "id,cf_stabilisation_atoms,summary,component",
                    "component": ["Stabilization", "Keywording"],
                    "resolution": "---",
                    "f1": "cf_stabilisation_atoms",
                    "o1": "anywords",
                    "v1": all_packages[i : i + batch_size],
                },
                doseq=True,
            )
            request = urllib.Request(
                url="https://bugs.gentoo.org/rest/bug?" + params,
                method="GET",
                headers={
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
            )
            with urllib.urlopen(request, timeout=30) as response:
                reply = json.loads(response.read().decode("utf-8"))
                all_bugs.extend(reply.get("bugs", []))

        for bug in all_bugs:
            bug_atoms = (
                parse_atom(line.split(" ", 1)[0]).unversioned_atom
                for line in map(str.strip, bug["cf_stabilisation_atoms"].splitlines())
                if line
            )
            bug_match = boolean.OrRestriction(*bug_atoms)
            exact_match = boolean.OrRestriction(
                *(
                    parse_atom(line.split(" ", 1)[0])
                    for line in map(str.strip, bug["cf_stabilisation_atoms"].splitlines())
                    if line
                )
            )
            for node in self.nodes:
                if bug.get("component") != _CATEGORY_META[node.category]["component"]:
                    continue
                if node.bugno is None and all(bug_match.match(pkg[0]) for pkg in node.pkgs):
                    is_exact_match = all(exact_match.match(pkg[0]) for pkg in node.pkgs)
                    self.out.write(
                        self.out.fg("yellow"),
                        f"Found https://bugs.gentoo.org/{bug['id']} for node {node}",
                        self.out.reset,
                        " (exact version match)" if is_exact_match else " (atom match)",
                    )
                    self.out.write(" -> bug summary: ", bug["summary"])
                    if is_exact_match:
                        node.bugno = bug["id"]
                    else:
                        if userquery(
                            "Not an exact match. Do you want to obsolete?",
                            self.out,
                            self.err,
                            default_answer=False,
                        ):
                            node.obsoletes.add(bug["id"])
                        else:
                            node.bugno = bug["id"]
                    has_output = True
        return has_output

    def file_bugs(self, api_key: str, auto_cc_arches: frozenset[str], block_bugs: list[int]):
        def observe(node: GraphNode):
            self.out.write(
                f"https://bugs.gentoo.org/{node.bugno} ",
                " | ".join(node.lines()),
                " depends on bugs ",
                {dep.bugno for dep in node.edges} or "{}",
            )
            self.out.flush()

        for node in self.starting_nodes:
            node.file_bug(api_key, auto_cc_arches, block_bugs, self.modified_repo, observe)


def _load_from_stdin(out: Formatter):
    if not sys.stdin.isatty():
        out.warn("No packages were specified, reading from stdin...")
        for line in sys.stdin.readlines():
            if line := line.split("#", 1)[0].strip():
                atom_str, *arches = line.split()
                yield atom_str, parserestrict.parse_match(atom_str), frozenset(arches)
        # reassign stdin to allow interactivity (currently only works for unix)
        sys.stdin = open("/dev/tty")
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
        if d.scan_existing_bugs(options.api_key):
            out.flush()
            has_output = True

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
                bugs.error("failed writing mask comment")
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
        out.write(out.fg("red"), "Nothing to do, exiting", out.reset)
        return 1
    counts = {
        meta["suffix"]: sum(node.category is category for node in pending)
        for category, meta in _CATEGORY_META.items()
    }
    summary = ", ".join(f"{count} {suffix}" for suffix, count in counts.items() if count)

    if not userquery(
        f"Continue and create {len(pending)} bugs ({summary})?", out, err, default_answer=False
    ):
        return 1

    disabled, enabled = options.auto_cc_arches
    blocks = list(frozenset(map(int, options.blocks)))
    d.file_bugs(options.api_key, frozenset(enabled).difference(disabled), blocks)
