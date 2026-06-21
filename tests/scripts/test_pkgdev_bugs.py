import itertools
import json
import os
import textwrap
from os.path import join as pjoin
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from pkgcore.ebuild.atom import atom

from pkgdev.scripts import pkgdev_bugs as bugs


def mk_pkg(repo, cpvstr, maintainers, **kwargs):
    kwargs.setdefault("KEYWORDS", ["~amd64"])
    pkgdir = os.path.dirname(repo.create_ebuild(cpvstr, **kwargs))
    # stub metadata
    with open(pjoin(pkgdir, "metadata.xml"), "w") as f:
        f.write(
            textwrap.dedent(
                f"""\
                    <?xml version="1.0" encoding="UTF-8"?>
                    <!DOCTYPE pkgmetadata SYSTEM "https://www.gentoo.org/dtd/metadata.dtd">
                    <pkgmetadata>
                        <maintainer type="person">
                            {" ".join(f"<email>{maintainer}@gentoo.org</email>" for maintainer in maintainers)}
                        </maintainer>
                    </pkgmetadata>
                """
            )
        )


def mk_repo(repo):
    mk_pkg(repo, "cat/u-0", ["dev1"])
    mk_pkg(repo, "cat/z-0", [], RDEPEND=["cat/u", "cat/x"])
    mk_pkg(repo, "cat/v-0", ["dev2"], RDEPEND="cat/x")
    mk_pkg(repo, "cat/y-0", ["dev1"], RDEPEND=["cat/z", "cat/v"])
    mk_pkg(repo, "cat/x-0", ["dev3"], RDEPEND="cat/y")
    mk_pkg(repo, "cat/w-0", ["dev3"], RDEPEND="cat/x")


class BugsSession:
    def __init__(self):
        self.counter = iter(itertools.count(1))
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, *_args): ...

    def read(self):
        return json.dumps({"id": next(self.counter)}).encode("utf-8")

    def __call__(self, request, *_args, **_kwargs):
        self.calls.append(json.loads(request.data))
        return self


class TestBugFiling:
    def test_bug_filing(self, repo):
        mk_repo(repo)
        session = BugsSession()
        pkg = max(repo.itermatch(atom("=cat/u-0")))
        with patch("pkgdev.scripts.pkgdev_bugs.urllib.urlopen", session):
            bugs.GraphNode(((pkg, {"*"}),)).file_bug("API", frozenset(), (), None)
        assert len(session.calls) == 1
        call = session.calls[0]
        assert call["Bugzilla_api_key"] == "API"
        assert call["summary"] == "cat/u-0: stablereq"
        assert call["assigned_to"] == "dev1@gentoo.org"
        assert not call["cc"]
        assert call["cf_stabilisation_atoms"] == "=cat/u-0 *"
        assert not call["depends_on"]

    def test_bug_filing_maintainer_needed(self, repo):
        mk_repo(repo)
        session = BugsSession()
        pkg = max(repo.itermatch(atom("=cat/z-0")))
        with patch("pkgdev.scripts.pkgdev_bugs.urllib.urlopen", session):
            bugs.GraphNode(((pkg, {"*"}),)).file_bug("API", frozenset(), (), None)
        assert len(session.calls) == 1
        call = session.calls[0]
        assert call["assigned_to"] == "maintainer-needed@gentoo.org"
        assert not call["cc"]

    def test_bug_filing_multiple_pkgs(self, repo):
        mk_repo(repo)
        session = BugsSession()
        pkgX = max(repo.itermatch(atom("=cat/x-0")))
        pkgY = max(repo.itermatch(atom("=cat/y-0")))
        pkgZ = max(repo.itermatch(atom("=cat/z-0")))
        dep = bugs.GraphNode((), bugno=2)
        node = bugs.GraphNode(((pkgX, {"*"}), (pkgY, {"*"}), (pkgZ, {"*"})))
        node.edges.add(dep)
        with patch("pkgdev.scripts.pkgdev_bugs.urllib.urlopen", session):
            node.file_bug("API", frozenset(), (), None)
        assert len(session.calls) == 1
        call = session.calls[0]
        assert call["summary"] == "cat/x-0, cat/y-0, cat/z-0: stablereq"
        assert call["assigned_to"] == "dev3@gentoo.org"
        assert call["cc"] == ["dev1@gentoo.org"]
        assert call["cf_stabilisation_atoms"] == "=cat/x-0 *\n=cat/y-0 *\n=cat/z-0 *"
        assert call["depends_on"] == [2]

    def test_keyword_bug_filing(self, repo):
        mk_repo(repo)
        session = BugsSession()
        pkg = max(repo.itermatch(atom("=cat/u-0")))
        node = bugs.GraphNode(((pkg, {"amd64"}),), category=bugs.NodeCategory.KEYWORDREQ)
        with patch("pkgdev.scripts.pkgdev_bugs.urllib.urlopen", session):
            node.file_bug("API", frozenset(), (), None)
        assert len(session.calls) == 1
        call = session.calls[0]
        # keywordreq bugs are version-less and request ~arch keywords
        assert call["component"] == "Keywording"
        assert call["summary"] == "cat/u: keywordreq"
        assert call["description"].startswith("Please keyword")
        assert call["cf_stabilisation_atoms"] == "cat/u ~amd64"


class TestSuggestedKeywords:
    def test_stablereq(self, repo):
        repo.create_ebuild("cat/a-1", KEYWORDS=["amd64", "x86"])
        repo.create_ebuild("cat/a-2", KEYWORDS=["amd64", "~x86"])
        pkg = max(repo.itermatch(atom("=cat/a-2")))
        # only ~arch keywords here that are stable on another version may be stabilized
        assert bugs._get_suggested_keywords(repo, pkg, streq=True) == frozenset({"x86"})

    def test_keywordreq(self, repo):
        repo.create_ebuild("cat/a-1", KEYWORDS=["~amd64", "~x86"])
        repo.create_ebuild("cat/a-2", KEYWORDS=["~amd64"])
        pkg = max(repo.itermatch(atom("=cat/a-2")))
        # keywords present on other versions but missing here are suggested
        assert bugs._get_suggested_keywords(repo, pkg, streq=False) == frozenset({"x86"})


class TestStableKeywordChain:
    def _mk_graph(self, repo, category=bugs.NodeCategory.STABLEREQ):
        # build a DependencyGraph without running its heavy __init__
        graph = bugs.DependencyGraph.__new__(bugs.DependencyGraph)
        graph.options = SimpleNamespace(repo=repo, category=category)
        graph.out = SimpleNamespace(write=lambda *a, **k: None, flush=lambda: None)
        graph.err = graph.out
        graph.nodes = set()
        graph.starting_nodes = set()
        graph.target_arches = {}
        return graph

    def test_stable_to_keyword_chain(self, repo):
        # the dependency has no amd64 keyword at all -> it must be keyworded before
        # it can be stabilized, producing a parent-stable -> dep-stable -> dep-keyword chain
        repo.create_ebuild("cat/parent-1", KEYWORDS=["amd64"])
        repo.create_ebuild("cat/parent-2", KEYWORDS=["~amd64"])
        repo.create_ebuild("cat/dep-1", KEYWORDS=["~x86"])
        parent = max(repo.itermatch(atom("=cat/parent-2")))
        dep = max(repo.itermatch(atom("=cat/dep-1")))

        graph = self._mk_graph(repo)
        graph.targets = (parent,)

        def fake_find_dependencies(pkg, keywords, stable=True):
            if pkg == parent:
                yield dep, {"amd64"}

        graph._find_dependencies = fake_find_dependencies
        graph.build_full_graph()

        by_key = {
            (p.versioned_atom.cpvstr, node.category): node
            for node in graph.nodes
            for p, _ in node.pkgs
        }
        parent_stable = by_key[("cat/parent-2", bugs.NodeCategory.STABLEREQ)]
        dep_stable = by_key[("cat/dep-1", bugs.NodeCategory.STABLEREQ)]
        dep_keyword = by_key[("cat/dep-1", bugs.NodeCategory.KEYWORDREQ)]
        # three distinct nodes, dep appears as both stable and keyword
        assert len(graph.nodes) == 3
        assert dep_stable is not dep_keyword
        # chain: parent-stable -> dep-stable -> dep-keyword
        assert dep_stable in parent_stable.edges
        assert dep_keyword in dep_stable.edges
        assert dep_keyword.is_keywordreq

    def test_new_arches_retrigger_dep_discovery(self, repo):
        # a dep reached from two parents with disjoint arches: the deps unique to the
        # second arch must still be discovered when the existing node is revisited
        repo.create_ebuild("cat/p1-1", KEYWORDS=["~amd64"])
        repo.create_ebuild("cat/p2-1", KEYWORDS=["~arm"])
        repo.create_ebuild("cat/d-1", KEYWORDS=["~amd64", "~arm"])
        repo.create_ebuild("cat/e-1", KEYWORDS=["~arm"])
        p1 = max(repo.itermatch(atom("=cat/p1-1")))
        p2 = max(repo.itermatch(atom("=cat/p2-1")))
        d = max(repo.itermatch(atom("=cat/d-1")))
        e = max(repo.itermatch(atom("=cat/e-1")))

        graph = self._mk_graph(repo)
        graph.targets = (p1, p2)
        graph.target_arches = {p1: frozenset({"amd64"}), p2: frozenset({"arm"})}

        def fake_find_dependencies(pkg, keywords, stable=True):
            if pkg == p1:
                yield d, {"amd64"}
            elif pkg == p2:
                yield d, {"arm"}
            elif pkg == d and "arm" in keywords:
                # cat/e is a dependency only relevant on arm
                yield e, {"arm"}

        graph._find_dependencies = fake_find_dependencies
        graph.build_full_graph()

        cpvs = {p.versioned_atom.cpvstr for node in graph.nodes for p, _ in node.pkgs}
        # cat/e is only reachable through d's arm dependency, discovered on revisit
        assert "cat/e-1" in cpvs

    def test_keyword_target_without_arches_errors(self, repo):
        # a keyword target with no other versions to derive arches from must error
        repo.create_ebuild("cat/a-1", KEYWORDS=["~amd64"])
        pkg = max(repo.itermatch(atom("=cat/a-1")))
        graph = self._mk_graph(repo, category=bugs.NodeCategory.KEYWORDREQ)
        graph.targets = (pkg,)
        graph._find_dependencies = lambda *a, **k: iter(())
        with pytest.raises(SystemExit):
            graph.build_full_graph()

    @pytest.mark.parametrize(
        "keywords",
        (
            ["~amd64", "-loong"],  # explicitly masked arch
            ["-*", "~amd64"],  # -* masks every non-listed arch
        ),
    )
    def test_keyword_masked_arch_errors(self, repo, keywords):
        # requesting a masked keyword is a hard error
        repo.create_ebuild("cat/a-1", KEYWORDS=keywords)
        pkg = max(repo.itermatch(atom("=cat/a-1")))
        graph = self._mk_graph(repo, category=bugs.NodeCategory.KEYWORDREQ)
        graph.targets = (pkg,)
        graph.target_arches = {pkg: frozenset({"loong"})}
        graph._find_dependencies = lambda *a, **k: iter(())
        with pytest.raises(SystemExit):
            graph.build_full_graph()

    def test_stable_dep_already_keyworded_no_chain(self, repo):
        # the dependency is ~amd64, so it can be stabilized directly without a keyword bug
        repo.create_ebuild("cat/parent-1", KEYWORDS=["amd64"])
        repo.create_ebuild("cat/parent-2", KEYWORDS=["~amd64"])
        repo.create_ebuild("cat/dep-1", KEYWORDS=["~amd64"])
        parent = max(repo.itermatch(atom("=cat/parent-2")))
        dep = max(repo.itermatch(atom("=cat/dep-1")))

        graph = self._mk_graph(repo)
        graph.targets = (parent,)

        def fake_find_dependencies(pkg, keywords, stable=True):
            if pkg == parent:
                yield dep, {"amd64"}

        graph._find_dependencies = fake_find_dependencies
        graph.build_full_graph()

        # only stablereq nodes, no keywordreq node
        assert all(not node.is_keywordreq for node in graph.nodes)
        assert len(graph.nodes) == 2

    def test_load_graph_toml_category(self, repo, tmp_path):
        repo.create_ebuild("cat/a-1", KEYWORDS=["~amd64"])
        graph = self._mk_graph(repo)
        graph.options = SimpleNamespace(repo=repo, search_repo=repo)
        toml_file = tmp_path / "graph.toml"
        toml_file.write_text(
            textwrap.dedent(
                """\
                [bug-1]
                category = "keywordreq"
                "=cat/a-1" = ["amd64"]

                [bug-2]
                category = "stablereq"
                "=cat/a-1" = ["amd64"]
                """
            )
        )
        graph.load_graph_toml(str(toml_file))
        assert {node.category for node in graph.nodes} == {
            bugs.NodeCategory.KEYWORDREQ,
            bugs.NodeCategory.STABLEREQ,
        }
