"""Automatic bugs filer"""

import json
import sys
import urllib.request as urllib
from collections import defaultdict
from functools import partial
from itertools import chain
from urllib.parse import urlencode

from pkgcheck import const as pkgcheck_const
from pkgcheck.addons import ArchesAddon, init_addon
from pkgcheck.addons.profiles import ProfileAddon
from pkgcheck.checks import visibility
from pkgcheck.scripts import argparse_actions
from pkgcore.ebuild.atom import atom
from pkgcore.ebuild.ebuild_src import package
from pkgcore.ebuild.errors import MalformedAtom
from pkgcore.ebuild.misc import sort_keywords
from pkgcore.repository import multiplex
from pkgcore.restrictions import boolean, packages, values
from pkgcore.test.misc import FakePkg
from pkgcore.util import commandline, parserestrict
from snakeoil.cli import arghparse
from snakeoil.cli.input import userquery
from snakeoil.formatters import Formatter

from ..cli import ArgumentParser
from .argparsers import _determine_cwd_repo, cwd_repo_argparser

bugs = ArgumentParser(
    prog="pkgdev bugs",
    description=__doc__,
    verbose=False,
    quiet=False,
    parents=(cwd_repo_argparser,),
)
bugs.add_argument(
    "--api-key",
    metavar="KEY",
    help="Bugzilla API key",
    docs="""
        The Bugzilla API key to use for authentication. Used mainly to overcome
        rate limiting done by bugzilla server. This tool doesn't perform any
        bug editing, just fetching info for the bug.
    """,
)
bugs.add_argument(
    "targets",
    metavar="target",
    nargs="*",
    action=commandline.StoreTarget,
    help="extended atom matching of packages",
)
bugs.add_argument(
    "--dot",
    help="path file where to save the graph in dot format",
)
bugs.add_argument(
    "--auto-cc-arches",
    action=arghparse.CommaSeparatedNegationsAppend,
    default=([], []),
    help="automatically add CC-ARCHES for the listed email addresses",
    docs="""
        Comma separated list of email addresses, for which automatically add
        CC-ARCHES if one of the maintainers matches the email address. If the
        package is maintainer-needed, always add CC-ARCHES.
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

ArchesAddon.mangle_argparser(bugs)
ProfileAddon.mangle_argparser(bugs)


@bugs.bind_delayed_default(1500, "target_repo")
def _validate_args(namespace, attr):
    _determine_cwd_repo(bugs, namespace)
    setattr(namespace, attr, namespace.repo)
    setattr(namespace, "verbosity", 1)
    setattr(namespace, "search_repo", multiplex.tree(*namespace.repo.trees))
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
    __slots__ = ("pkgs", "edges", "bugno")

    def __init__(self, pkgs: tuple[tuple[package, set[str]], ...], bugno=None):
        self.pkgs = pkgs
        self.edges: set[GraphNode] = set()
        self.bugno = bugno

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

    def file_bug(self, api_key: str, auto_cc_arches: frozenset[str], observer=None) -> int:
        if self.bugno is not None:
            return self.bugno
        for dep in self.edges:
            if dep.bugno is None:
                dep.file_bug(api_key, auto_cc_arches, observer)
        maintainers = dict.fromkeys(
            maintainer.email for pkg, _ in self.pkgs for maintainer in pkg.maintainers
        )
        if not maintainers or "*" in auto_cc_arches or auto_cc_arches.intersection(maintainers):
            keywords = ["CC-ARCHES"]
        else:
            keywords = []
        maintainers = tuple(maintainers) or ("maintainer-needed@gentoo.org",)

        request_data = dict(
            Bugzilla_api_key=api_key,
            product="Gentoo Linux",
            component="Stabilization",
            severity="enhancement",
            version="unspecified",
            summary=f"{', '.join(pkg.versioned_atom.cpvstr for pkg, _ in self.pkgs)}: stablereq",
            description="Please stabilize",
            keywords=keywords,
            cf_stabilisation_atoms="\n".join(self.lines()),
            assigned_to=maintainers[0],
            cc=maintainers[1:],
            depends_on=list({dep.bugno for dep in self.edges}),
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
        self.profile_addon: ProfileAddon = init_addon(ProfileAddon, options)

        self.nodes: set[GraphNode] = set()
        self.starting_nodes: set[GraphNode] = set()

    def mk_fake_pkg(self, pkg: package, keywords: set[str]):
        return FakePkg(
            cpv=pkg.cpvstr,
            eapi=str(pkg.eapi),
            iuse=pkg.iuse,
            repo=pkg.repo,
            keywords=tuple(keywords),
            data={attr: str(getattr(pkg, attr.lower())) for attr in pkg.eapi.dep_keys},
        )

    def find_best_match(self, restrict, pkgset: list[package]) -> package:
        restrict = boolean.AndRestriction(
            *restrict,
            packages.PackageRestriction("properties", values.ContainmentMatch("live", negate=True)),
        )
        # prefer using already selected packages in graph
        all_pkgs = (pkg for node in self.nodes for pkg, _ in node.pkgs)
        if intersect := tuple(filter(restrict.match, all_pkgs)):
            return max(intersect)
        matches = sorted(filter(restrict.match, pkgset), reverse=True)
        for match in matches:
            if not all(keyword.startswith("~") for keyword in match.keywords):
                return match
        return matches[0]

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
                        self.err.error(
                            f"unable to find match for restrictions: {deps_str}",
                        )
                        raise
                    results[match].add(keyword)
                yield from results.items()

    def build_full_graph(self, targets: list[package]):
        check_nodes = [(pkg, set()) for pkg in targets]

        vertices: dict[package, GraphNode] = {}
        edges = []
        while len(check_nodes):
            pkg, keywords = check_nodes.pop(0)
            if pkg in vertices:
                vertices[pkg].pkgs[0][1].update(keywords)
                continue

            keywords.update(_get_suggested_keywords(self.options.repo, pkg))
            assert (
                keywords
            ), f"no keywords for {pkg.versioned_atom}, currently unsupported by tool: https://github.com/pkgcore/pkgdev/issues/123"
            self.nodes.add(new_node := GraphNode(((pkg, keywords),)))
            vertices[pkg] = new_node
            self.out.write(
                f"Checking {pkg.versioned_atom} on {' '.join(sort_keywords(keywords))!r}"
            )
            self.out.flush()

            for dep, keywords in self._find_dependencies(pkg, keywords):
                edges.append((pkg, dep))
                check_nodes.append((dep, keywords))

        for src, dst in edges:
            vertices[src].edges.add(vertices[dst])
        self.starting_nodes = {vertices[starting_node] for starting_node in targets}

    def output_dot(self, dot_file):
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
                        for pkg in node.pkgs
                        for pkgver in repo.match(pkg[0].unversioned_atom)
                    )
                )
                if existing_keywords & frozenset().union(*(pkg[1] for pkg in node.pkgs)):
                    continue  # not fully new keywords
                orig = next(iter(origs))
                self.out.write(f"Merging {node} into {orig}")
                self.merge_nodes((orig, node))
                found_someone = True
                break

    def scan_existing_bugs(self, api_key: str):
        params = urlencode(
            {
                "Bugzilla_api_key": api_key,
                "include_fields": "id,cf_stabilisation_atoms",
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
                    break

    def file_bugs(self, api_key: str, auto_cc_arches: frozenset[str]):
        def observe(node: GraphNode):
            self.out.write(
                f"https://bugs.gentoo.org/{node.bugno} ",
                " | ".join(node.lines()),
                " depends on bugs ",
                {dep.bugno for dep in node.edges},
            )
            self.out.flush()

        for node in self.starting_nodes:
            node.file_bug(api_key, auto_cc_arches, observe)


def _load_from_stdin(out: Formatter, err: Formatter):
    if not sys.stdin.isatty():
        out.warn("No packages were specified, reading from stdin...")
        for line in sys.stdin.readlines():
            if line := line.split("#", 1)[0].strip():
                yield line, parserestrict.parse_match(line)
        # reassign stdin to allow interactivity (currently only works for unix)
        sys.stdin = open("/dev/tty")
    else:
        raise arghparse.ArgumentError(None, "reading from stdin is only valid when piping data in")


def _parse_targets(search_repo, targets):
    for _, target in targets:
        try:
            yield max(search_repo.itermatch(target))
        except ValueError:
            raise ValueError(f"Restriction {target} has no match in repository")


@bugs.bind_main_func
def main(options, out: Formatter, err: Formatter):
    search_repo = options.search_repo
    options.targets = options.targets or list(_load_from_stdin(out, err))
    targets = list(_parse_targets(search_repo, options.targets))
    d = DependencyGraph(out, err, options)
    d.build_full_graph(targets)
    d.merge_cycles()
    d.merge_new_keywords_children()

    for node in d.nodes:
        node.cleanup_keywords(search_repo)

    if userquery("Check for open bugs matching current graph?", out, err, default_answer=False):
        d.scan_existing_bugs(options.api_key)

    if options.dot is not None:
        d.output_dot(options.dot)
        out.write(out.fg("green"), f"Dot file written to {options.dot}", out.reset)

    if not userquery(
        f"Continue and create {len(d.nodes)} stablereq bugs?", out, err, default_answer=False
    ):
        return 1

    if options.api_key is None:
        err.write(out.fg("red"), "No API key provided, exiting", out.reset)
        return 1

    disabled, enabled = options.auto_cc_arches
    d.file_bugs(options.api_key, frozenset(enabled).difference(disabled))
