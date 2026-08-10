import os
import textwrap
from os.path import join as pjoin
from types import SimpleNamespace

import pytest
from pkgcore.bugzilla import BugCategory
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


class TestBugFiling:
    def test_bug_filing(self, repo, bugzilla_cassette):
        mk_repo(repo)
        bugzilla_cassette.creates_bugs()
        pkg = max(repo.itermatch(atom("=cat/u-0")))
        bugs.GraphNode(((pkg, {"*"}),)).file_bug(
            bugzilla_cassette.client(api_key="API"), frozenset(), (), None
        )
        assert len(bugzilla_cassette.calls) == 1
        call = bugzilla_cassette.calls[0].body
        assert call["Bugzilla_api_key"] == "API"
        assert call["summary"] == "cat/u-0: stablereq"
        assert call["assigned_to"] == "dev1@gentoo.org"
        assert "cc" not in call
        assert call["cf_stabilisation_atoms"] == "=cat/u-0 *"
        assert "depends_on" not in call

    def test_bug_filing_maintainer_needed(self, repo, bugzilla_cassette):
        mk_repo(repo)
        bugzilla_cassette.creates_bugs()
        pkg = max(repo.itermatch(atom("=cat/z-0")))
        bugs.GraphNode(((pkg, {"*"}),)).file_bug(
            bugzilla_cassette.client(api_key="API"), frozenset(), (), None
        )
        assert len(bugzilla_cassette.calls) == 1
        call = bugzilla_cassette.calls[0].body
        assert call["assigned_to"] == "maintainer-needed@gentoo.org"
        assert "cc" not in call

    def test_bug_filing_multiple_pkgs(self, repo, bugzilla_cassette):
        mk_repo(repo)
        bugzilla_cassette.creates_bugs()
        pkgX = max(repo.itermatch(atom("=cat/x-0")))
        pkgY = max(repo.itermatch(atom("=cat/y-0")))
        pkgZ = max(repo.itermatch(atom("=cat/z-0")))
        dep = bugs.GraphNode((), bugno=2)
        node = bugs.GraphNode(((pkgX, {"*"}), (pkgY, {"*"}), (pkgZ, {"*"})))
        node.edges.add(dep)
        node.file_bug(bugzilla_cassette.client(api_key="API"), frozenset(), (), None)
        assert len(bugzilla_cassette.calls) == 1
        call = bugzilla_cassette.calls[0].body
        assert call["summary"] == "cat/x-0, cat/y-0, cat/z-0: stablereq"
        assert call["assigned_to"] == "dev3@gentoo.org"
        assert call["cc"] == ["dev1@gentoo.org"]
        assert call["cf_stabilisation_atoms"] == "=cat/x-0 *\n=cat/y-0 *\n=cat/z-0 *"
        assert call["depends_on"] == [2]

    def test_keyword_bug_filing(self, repo, bugzilla_cassette):
        mk_repo(repo)
        bugzilla_cassette.creates_bugs()
        pkg = max(repo.itermatch(atom("=cat/u-0")))
        node = bugs.GraphNode(((pkg, {"amd64"}),), category=BugCategory.KEYWORDREQ)
        node.file_bug(bugzilla_cassette.client(api_key="API"), frozenset(), (), None)
        assert len(bugzilla_cassette.calls) == 1
        call = bugzilla_cassette.calls[0].body
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
    def _mk_graph(self, repo, category=BugCategory.STABLEREQ):
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
        parent_stable = by_key[("cat/parent-2", BugCategory.STABLEREQ)]
        dep_stable = by_key[("cat/dep-1", BugCategory.STABLEREQ)]
        dep_keyword = by_key[("cat/dep-1", BugCategory.KEYWORDREQ)]
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
        graph = self._mk_graph(repo, category=BugCategory.KEYWORDREQ)
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
        graph = self._mk_graph(repo, category=BugCategory.KEYWORDREQ)
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
            BugCategory.KEYWORDREQ,
            BugCategory.STABLEREQ,
        }

    def test_edit_graph_roundtrip_preserves_starting_node(self, repo):
        # regression test for https://github.com/pkgcore/pkgdev/issues/218 :
        # a target node with an already-filed dependency must remain a starting
        # node after an output_graph_toml() -> load_graph_toml() round trip
        repo.create_ebuild("cat/a-1", KEYWORDS=["~amd64"])
        repo.create_ebuild("cat/b-1", KEYWORDS=["~amd64"])
        a = max(repo.itermatch(atom("=cat/a-1")))
        b = max(repo.itermatch(atom("=cat/b-1")))

        graph = self._mk_graph(repo)
        graph.options = SimpleNamespace(repo=repo, search_repo=repo)
        graph.auto_cc_arches = frozenset()
        graph.modified_repo = SimpleNamespace(itermatch=lambda *a, **k: iter(()))
        graph.added_repo = SimpleNamespace(itermatch=lambda *a, **k: iter(()))

        # dep already has a bug filed; target doesn't yet
        dep_node = bugs.GraphNode(((b, {"amd64"}),), bugno=100)
        target_node = bugs.GraphNode(((a, {"amd64"}),))
        target_node.edges.add(dep_node)
        graph.nodes = {target_node, dep_node}
        graph.starting_nodes = {target_node}

        toml_file = graph.output_graph_toml()
        graph.load_graph_toml(toml_file.name)

        assert len(graph.starting_nodes) == 1
        (loaded_target,) = graph.starting_nodes
        assert {p.cpvstr for p, _ in loaded_target.pkgs} == {"cat/a-1"}


class TestObsoletingBugs:
    def _mk_graph(self, repo, monkeypatch, answer=True):
        graph = bugs.DependencyGraph.__new__(bugs.DependencyGraph)
        graph.options = SimpleNamespace(repo=repo, search_repo=repo)
        graph.out = SimpleNamespace(
            write=lambda *a, **k: None, flush=lambda: None, fg=lambda *a: "", reset=""
        )
        graph.err = graph.out
        graph.nodes = set()
        graph.starting_nodes = set()
        graph.modified_repo = None
        monkeypatch.setattr(bugs, "userquery", lambda *a, **k: answer)
        return graph

    @staticmethod
    def _bug(bug_id, atoms):
        return {
            "id": bug_id,
            "summary": f"bug {bug_id}",
            "product": "Gentoo Linux",
            "component": "Stabilization",
            "cf_stabilisation_atoms": atoms,
        }

    @pytest.mark.parametrize("reverse", (False, True))
    def test_obsolete_older_bug_with_exact_match(
        self, repo, bugzilla_cassette, monkeypatch, reverse
    ):
        # regression test: an old atom match and a new exact match for the same node
        # must obsolete the old one, no matter which order bugzilla returns them in
        repo.create_ebuild("cat/a-1", KEYWORDS=["~amd64"])
        pkg = max(repo.itermatch(atom("=cat/a-1")))

        graph = self._mk_graph(repo, monkeypatch)
        node = bugs.GraphNode(((pkg, {"amd64"}),))
        graph.nodes = {node}
        graph.starting_nodes = {node}

        found = [self._bug(100, "=cat/a-0 amd64"), self._bug(200, "=cat/a-1 amd64")]
        bugzilla_cassette.expect_bugs(*reversed(found) if reverse else found)
        assert graph.scan_existing_bugs(bugzilla_cassette.client())
        assert node.bugno == 200
        assert node.obsoletes == {100}

    def test_obsoletion_of_existing_bug_is_filed(self, repo, bugzilla_cassette, monkeypatch):
        # nothing new to file, but the matched bug still has to obsolete the old one
        repo.create_ebuild("cat/a-1", KEYWORDS=["~amd64"])
        pkg = max(repo.itermatch(atom("=cat/a-1")))

        graph = self._mk_graph(repo, monkeypatch)
        node = bugs.GraphNode(((pkg, {"amd64"}),), bugno=200)
        node.obsoletes.add(100)
        graph.nodes = {node}
        graph.starting_nodes = {node}

        bugzilla_cassette.expect_changed(100)
        graph.file_bugs(bugzilla_cassette.client(), frozenset(), [])

        assert len(bugzilla_cassette.calls) == 1
        call = bugzilla_cassette.calls[0]
        assert call.method == "PUT"
        assert call.body["ids"] == [100]
        assert call.body["resolution"] == "OBSOLETE"
        assert call.body["see_also"] == {"add": ["https://bugs.gentoo.org/200"]}
        # consumed, so a second pass doesn't repeat the update
        assert not node.obsoletes

    def test_merge_nodes_keeps_obsoletes(self, repo, monkeypatch):
        repo.create_ebuild("cat/a-1", KEYWORDS=["~amd64"])
        repo.create_ebuild("cat/b-1", KEYWORDS=["~amd64"])
        a = max(repo.itermatch(atom("=cat/a-1")))
        b = max(repo.itermatch(atom("=cat/b-1")))

        graph = self._mk_graph(repo, monkeypatch)
        first = bugs.GraphNode(((a, {"amd64"}),))
        first.obsoletes.add(100)
        second = bugs.GraphNode(((b, {"amd64"}),))
        second.obsoletes.add(101)
        graph.nodes = {first, second}

        merged = graph.merge_nodes((first, second))
        assert merged.obsoletes == {100, 101}
