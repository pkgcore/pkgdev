"""Automatic bugs filer"""

import contextlib
import json
import os
import shlex
import subprocess
import sys
import tempfile
import urllib.request as urllib
from collections import defaultdict
from datetime import datetime
from functools import partial
from itertools import chain
from urllib.parse import urlencode

from pkgcheck import const as pkgcheck_const
from pkgcheck.addons import ArchesAddon, init_addon
from pkgcheck.addons.profiles import ProfileAddon
from pkgcheck.addons.git import GitAddon, GitAddedRepo, GitModifiedRepo
from pkgcheck.checks import visibility, stablereq
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
from snakeoil.osutils import pjoin

from ..cli import ArgumentParser
from .argparsers import _determine_cwd_repo, cwd_repo_argparser, BugzillaApiKey

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

bugs = ArgumentParser(
    prog="pkgdev bugs",
    description=__doc__,
    verbose=False,
    quiet=False,
    parents=(cwd_repo_argparser,),
)
BugzillaApiKey.mangle_argparser(bugs)
bugs.add_argument(
    "targets",
    metavar="target",
    nargs="*",
    action=commandline.StoreTarget,
    use_sets="sets",
    help="extended atom matching of packages",
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
    if namespace.keywording:
        parser.error("keywording is not implemented yet, sorry")


def _get_suggested_keywords(repo, pkg: package):
    match_keywords = {
        x
        for pkgver in repo.match(pkg.unversioned_atom)
        for x in pkgver.keywords
        if x[0] not in "-~"
    }

    # limit stablereq to whatever is ~arch right now
    match_keywords.intersection_update(x.lstrip("~") for x in pkg.keywords if x[0] == "~")

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
    __slots__ = ("pkgs", "edges", "bugno", "summary", "cc_arches")

    def __init__(self, pkgs: tuple[tuple[package, set[str]], ...], bugno=None):
        self.pkgs = pkgs
        self.edges: set[GraphNode] = set()
        self.bugno = bugno
        self.summary = ""
        self.cc_arches = None

    def __eq__(self, __o: object):
        return self is __o

    def __hash__(self):
        return hash(id(self))

    def __str__(self):
        return ", ".join(str(pkg.versioned_atom) for pkg, _ in self.pkgs)

    def __repr__(self):
        return str(self)

    def lines(self):
        for pkg, keywords in self.pkgs:
            yield f"{pkg.versioned_atom} {' '.join(sort_keywords(keywords))}"

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
            suggested = _get_suggested_keywords(repo, pkg)
            if keywords == set(suggested):
                keywords.clear()
                keywords.add("*")

    @property
    def bug_summary(self):
        if self.summary:
            return self.summary
        summary = f"{', '.join(pkg.versioned_atom.cpvstr for pkg, _ in self.pkgs)}: stablereq"
        if len(summary) > 90 and len(self.pkgs) > 1:
            return f"{self.pkgs[0][0].versioned_atom.cpvstr} and friends: stablereq"
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

        description = ["Please stabilize", ""]
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
            component="Stabilization",
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
        return self.bugno


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

        git_addon = init_addon(GitAddon, options)
        self.added_repo = git_addon.cached_repo(GitAddedRepo)
        self.modified_repo = git_addon.cached_repo(GitModifiedRepo)
        self.stablereq_check = stablereq.StableRequestCheck(self.options, git_addon=git_addon)

    def mk_fake_pkg(self, pkg: package, keywords: set[str]):
        return FakePkg(
            cpv=pkg.cpvstr,
            eapi=str(pkg.eapi),
            iuse=pkg.iuse,
            repo=pkg.repo,
            keywords=tuple(keywords),
            data={attr: str(getattr(pkg, attr.lower())) for attr in pkg.eapi.dep_keys},
        )

    def find_best_match(self, restrict, pkgset: list[package], prefer_semi_stable=True) -> package:
        restrict = boolean.AndRestriction(
            *restrict,
            packages.PackageRestriction("properties", values.ContainmentMatch("live", negate=True)),
        )
        # prefer using user selected targets
        if intersect := tuple(filter(restrict.match, self.targets)):
            return max(intersect)
        # prefer using already selected packages in graph
        all_pkgs = (pkg for node in self.nodes for pkg, _ in node.pkgs)
        if intersect := tuple(filter(restrict.match, all_pkgs)):
            return max(intersect)
        matches = sorted(filter(restrict.match, pkgset), reverse=True)
        if prefer_semi_stable:
            for match in matches:
                if not all(keyword.startswith("~") for keyword in match.keywords):
                    return match
        return matches[0]

    def extend_targets_stable_groups(self, groups):
        stabilization_groups = self.options.repo.stabilization_groups
        for group in groups:
            for pkg in stabilization_groups[group]:
                try:
                    yield None, pkg
                except (ValueError, IndexError):
                    self.err.write(f"Unable to find match for {pkg.unversioned_atom}")

    def _extend_projects(self, disabled, enabled):
        members = defaultdict(set)
        self.out.write("Fetching projects.xml")
        self.out.flush()
        with urllib.urlopen("https://api.gentoo.org/metastructure/projects.xml", timeout=30) as f:
            for email, project in ProjectsXml(bytes_data_source(f.read())).projects.items():
                for member in project.members:
                    members[member.email].add(email)

        disabled = frozenset(disabled).union(*(members[email] for email in disabled))
        enabled = frozenset(enabled).union(*(members[email] for email in enabled))
        return disabled, enabled

    def extend_maintainers(self):
        disabled, enabled = self.options.find_by_maintainer
        if self.options.projects:
            disabled, enabled = self._extend_projects(disabled, enabled)
        emails = frozenset(enabled).difference(disabled)
        if not emails:
            return
        search_repo = self.options.search_repo
        self.out.write("Searching for packages maintained by: ", ", ".join(emails))
        self.out.flush()
        for cat, pkgs in search_repo.packages.items():
            for pkg in pkgs:
                xml = LocalMetadataXml(pjoin(search_repo.location[0], cat, pkg, "metadata.xml"))
                if emails.intersection(m.email for m in xml.maintainers):
                    yield None, parserestrict.parse_match(f"{cat}/{pkg}")

    def _find_dependencies(self, pkg: package, keywords: set[str]):
        check = visibility.VisibilityCheck(self.options, profile_addon=self.profile_addon)

        issues: dict[str, dict[str, set[atom]]] = defaultdict(partial(defaultdict, set))
        for res in check.feed(self.mk_fake_pkg(pkg, keywords)):
            if isinstance(res, visibility.NonsolvableDeps):
                for dep in res.deps:
                    dep = atom(dep).no_usedeps
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
                    except (ValueError, IndexError):
                        deps_str = " , ".join(map(str, deps))
                        bugs.error(
                            f"unable to find match for restrictions: {deps_str}",
                            status=3,
                        )
                    results[match].add(keyword)
                yield from results.items()

    def load_targets(self, targets: list[tuple[str, str]]):
        result = []
        search_repo = self.options.search_repo
        for _, target in targets:
            try:
                pkgset = search_repo.match(target)
                if self.options.filter_stablereqs:
                    for res in self.stablereq_check.feed(sorted(pkgset)):
                        if isinstance(res, stablereq.StableRequest):
                            target = atom(f"={res.category}/{res.package}-{res.version}")
                            break
                    else:  # no stablereq
                        continue
                result.append(self.find_best_match([target], pkgset, False))
            except (ValueError, IndexError):
                bugs.error(f"Restriction {target} has no match in repository", status=3)
        self.targets = tuple(result)

    def build_full_graph(self):
        check_nodes = [(pkg, set(), "") for pkg in self.targets]

        vertices: dict[package, GraphNode] = {}
        edges = []
        while len(check_nodes):
            pkg, keywords, reason = check_nodes.pop(0)
            if pkg in vertices:
                vertices[pkg].pkgs[0][1].update(keywords)
                continue

            pkg_has_stable = any(x[0] not in "-~" for x in pkg.keywords)
            keywords.update(_get_suggested_keywords(self.options.repo, pkg))
            if pkg_has_stable and not keywords:  # package already done
                self.out.write(f"Nothing to stable for {pkg.unversioned_atom}")
                continue
            assert (
                keywords
            ), f"no keywords for {pkg.versioned_atom}, currently unsupported by tool: https://github.com/pkgcore/pkgdev/issues/123"
            self.nodes.add(new_node := GraphNode(((pkg, keywords),)))
            vertices[pkg] = new_node
            if reason:
                reason = f" [added for {reason}]"
            self.out.write(
                f"Checking {pkg.versioned_atom} on {' '.join(sort_keywords(keywords))!r}{reason}"
            )
            self.out.flush()

            for dep, keywords in self._find_dependencies(pkg, keywords):
                edges.append((pkg, dep))
                check_nodes.append((dep, keywords, str(pkg.versioned_atom)))

        for src, dst in edges:
            vertices[src].edges.add(vertices[dst])
        self.starting_nodes = {
            vertices[starting_node] for starting_node in self.targets if starting_node in vertices
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
        self.auto_cc_arches
        bugs = dict(enumerate(self.nodes, start=1))
        reverse_bugs = {node: bugno for bugno, node in bugs.items()}

        toml = tempfile.NamedTemporaryFile(mode="w", suffix=".toml")
        for bugno, node in bugs.items():
            if node.bugno is not None:
                continue  # already filed
            toml.write(f"[bug-{bugno}]\n")
            toml.write(f'summary = "{node.bug_summary}"\n')
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
            new_bugs[node_name] = GraphNode(pkgs)
        for node_name, data_node in data.items():
            new_bugs[node_name].summary = data_node.get("summary", "")
            new_bugs[node_name].cc_arches = data_node.get("cc_arches", None)
            for dep in data_node.get("depends", ()):
                if isinstance(dep, int):
                    new_bugs[node_name].edges.add(new_bugs.setdefault(dep, GraphNode((), dep)))
                elif new_bugs.get(dep) is not None:
                    new_bugs[node_name].edges.add(new_bugs[dep])
                else:
                    bugs.error(f"[{node_name}]['depends']: unknown dependency {dep!r}")
        self.nodes = set(new_bugs.values())
        self.starting_nodes = {node for node in self.nodes if not node.edges}

    def merge_nodes(self, nodes: tuple[GraphNode, ...]) -> GraphNode:
        self.nodes.difference_update(nodes)
        is_start = bool(self.starting_nodes.intersection(nodes))
        self.starting_nodes.difference_update(nodes)
        new_node = GraphNode(list(chain.from_iterable(n.pkgs for n in nodes)))

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
                self.out.write(f"Merging {node} into {orig}")
                self.merge_nodes((orig, node))
                found_someone = True
                break

    def merge_stabilization_groups(self):
        for group, pkgs in self.options.repo.stabilization_groups.items():
            restrict = packages.OrRestriction(*pkgs)
            mergable = tuple(
                node for node in self.nodes if any(restrict.match(pkg) for pkg, _ in node.pkgs)
            )
            if mergable:
                self.out.write(f"Merging @{group} group nodes: {mergable}")
                self.merge_nodes(mergable)

    def scan_existing_bugs(self, api_key: str):
        params = urlencode(
            {
                "Bugzilla_api_key": api_key,
                "include_fields": "id,cf_stabilisation_atoms,summary",
                "component": "Stabilization",
                "resolution": "---",
                "f1": "cf_stabilisation_atoms",
                "o1": "anywords",
                "v1": {pkg[0].unversioned_atom for node in self.nodes for pkg in node.pkgs},
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
        for bug in reply["bugs"]:
            bug_atoms = (
                parse_atom(line.split(" ", 1)[0]).unversioned_atom
                for line in map(str.strip, bug["cf_stabilisation_atoms"].splitlines())
                if line
            )
            bug_match = boolean.OrRestriction(*bug_atoms)
            for node in self.nodes:
                if node.bugno is None and all(bug_match.match(pkg[0]) for pkg in node.pkgs):
                    node.bugno = bug["id"]
                    self.out.write(
                        self.out.fg("yellow"),
                        f"Found https://bugs.gentoo.org/{node.bugno} for node {node}",
                        self.out.reset,
                    )
                    self.out.write(" -> bug summary: ", bug["summary"])
                    break

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
                yield line, parserestrict.parse_match(line)
        # reassign stdin to allow interactivity (currently only works for unix)
        sys.stdin = open("/dev/tty")
    else:
        bugs.error("reading from stdin is only valid when piping data in")


@bugs.bind_main_func
def main(options, out: Formatter, err: Formatter):
    search_repo = options.search_repo
    options.targets = options.targets or []
    d = DependencyGraph(out, err, options)
    options.targets.extend(d.extend_maintainers())
    options.targets.extend(d.extend_targets_stable_groups(options.sets or ()))
    if not options.targets:
        options.targets = list(_load_from_stdin(out))
    d.load_targets(options.targets)
    d.build_full_graph()
    d.merge_stabilization_groups()
    d.merge_cycles()
    d.merge_new_keywords_children()

    if not d.nodes:
        out.write(out.fg("red"), "Nothing to do, exiting", out.reset)
        return 1

    if userquery("Check for open bugs matching current graph?", out, err, default_answer=False):
        d.scan_existing_bugs(options.api_key)

    if options.edit_graph:
        toml = d.output_graph_toml()

    for node in d.nodes:
        node.cleanup_keywords(search_repo)

    if options.dot is not None:
        d.output_dot(options.dot)
        out.write(out.fg("green"), f"Dot file written to {options.dot}", out.reset)
        out.flush()

    if options.edit_graph:
        editor = shlex.split(os.environ.get("VISUAL", os.environ.get("EDITOR", "nano")))
        try:
            subprocess.run(editor + [toml.name], check=True)
        except subprocess.CalledProcessError:
            bugs.error("failed writing mask comment")
        except FileNotFoundError:
            bugs.error(f"nonexistent editor: {editor[0]!r}")
        d.load_graph_toml(toml.name)
        for node in d.nodes:
            node.cleanup_keywords(search_repo)

        if options.dot is not None:
            d.output_dot(options.dot)
            out.write(out.fg("green"), f"Dot file written to {options.dot}", out.reset)
            out.flush()

    bugs_count = len(tuple(node for node in d.nodes if node.bugno is None))
    if bugs_count == 0:
        out.write(out.fg("red"), "Nothing to do, exiting", out.reset)
        return 1

    if not userquery(
        f"Continue and create {bugs_count} stablereq bugs?", out, err, default_answer=False
    ):
        return 1

    if options.api_key is None:
        err.write(out.fg("red"), "No API key provided, exiting", out.reset)
        return 1

    disabled, enabled = options.auto_cc_arches
    blocks = list(frozenset(map(int, options.blocks)))
    d.file_bugs(options.api_key, frozenset(enabled).difference(disabled), blocks)
