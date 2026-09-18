import os
import textwrap
from contextlib import chdir
from os.path import join as pjoin
from types import SimpleNamespace

import pytest
from pkgcore.bugzilla import BugCategory, BugzillaError
from pkgcore.ebuild.atom import atom
from pkgcore.util import parserestrict

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


def mk_profiles(repo, *arches: str):
    # a profile per arch, as the visibility check needs to resolve deps somewhere
    repo.arches.update(arches)
    repo.create_profiles(
        SimpleNamespace(
            path=f"default/{arch}",
            arch=arch,
            status="stable",
            deprecated=False,
            eapi="5",
            defaults=[f"ARCH={arch}", f"ACCEPT_KEYWORDS={arch}"],
        )
        for arch in arches
    )
    repo.sync()


def mk_group(repo, group: str, *pkgs):
    group_dir = pjoin(repo.location, "metadata", "stabilization-groups")
    os.makedirs(group_dir, exist_ok=True)
    with open(pjoin(group_dir, f"{group}.group"), "w") as f:
        f.write("\n".join(pkgs) + "\n")
    repo.sync()


def mk_out():
    """Formatter stub recording everything written to it."""
    lines = []
    write = lambda *args, **kwargs: lines.append("".join(map(str, args)))
    out = SimpleNamespace(write=write, warn=write, flush=lambda: None, fg=lambda *a: "", reset="")
    out.lines = lines
    return out


def mk_graph(repo, category=BugCategory.STABLEREQ):
    # build a DependencyGraph without running its heavy __init__
    graph = bugs.DependencyGraph.__new__(bugs.DependencyGraph)
    graph.options = SimpleNamespace(
        repo=repo, search_repo=repo, category=category, filter_stablereqs=False
    )
    graph.out = graph.err = mk_out()
    graph.nodes = set()
    graph.starting_nodes = set()
    graph.targets = ()
    graph.target_arches = {}
    graph.auto_cc_arches = frozenset()
    graph.modified_repo = graph.added_repo = SimpleNamespace(itermatch=lambda *a, **k: iter(()))
    graph.stablereq_check = SimpleNamespace(feed=lambda pkgs: iter(()))
    graph._stablereq_due = {}
    return graph


def mk_bug(bug_id, atoms, component="Stabilization"):
    return {
        "id": bug_id,
        "summary": f"bug {bug_id}",
        "product": "Gentoo Linux",
        "component": component,
        "cf_stabilisation_atoms": atoms,
    }


def set_userquery(monkeypatch, yesno=True, choice="reuse"):
    # the atom match prompt offers three choices, every other one is yes/no
    monkeypatch.setattr(bugs, "userquery", lambda *a, **k: choice if k.get("responses") else yesno)


@pytest.fixture
def bugs_options(repo, make_git_repo, tool):
    """Parse ``pkgdev bugs`` arguments against a real ebuild repo."""
    make_git_repo(repo.location, commit=True)

    def _parse(*args):
        with chdir(repo.location):
            options, _func = tool.parse_args(["bugs", *args])
        return options

    return _parse


@pytest.fixture
def real_graph(bugs_options):
    """Build a DependencyGraph the way the command builds it."""

    def _graph(*args):
        return bugs.DependencyGraph(mk_out(), mk_out(), bugs_options(*args))

    return _graph


class TestPkgdevBugsParseArgs:
    def test_non_repo_cwd(self, capsys, tool, tmp_path):
        with pytest.raises(SystemExit), chdir(str(tmp_path)):
            tool.parse_args(["bugs"])
        _out, err = capsys.readouterr()
        assert err.strip() == "pkgdev bugs: error: not in ebuild repo"

    def test_keywording_with_filter_stablereqs(self, capsys, bugs_options):
        with pytest.raises(SystemExit):
            bugs_options("--keywording", "--filter-stablereqs")
        _out, err = capsys.readouterr()
        assert "--keywording is incompatible with --filter-stablereqs" in err

    def test_default_category(self, bugs_options):
        assert bugs_options().category is BugCategory.STABLEREQ
        assert bugs_options("--keywording").category is BugCategory.KEYWORDREQ

    def test_arches_after_the_atom(self, repo, bugs_options):
        repo.create_ebuild("cat/a-1", KEYWORDS=["~amd64"])
        ((token, _restrict, arches),) = bugs_options("=cat/a-1 amd64 x86").targets
        assert token == "=cat/a-1"
        assert arches == frozenset({"amd64", "x86"})

    def test_atom_without_arches(self, repo, bugs_options):
        repo.create_ebuild("cat/a-1", KEYWORDS=["~amd64"])
        ((_token, _restrict, arches),) = bugs_options("=cat/a-1").targets
        assert arches == frozenset()

    def test_stabilization_group_target(self, bugs_options):
        options = bugs_options("@rust", "cat/a")
        assert options.sets == ["rust"]
        assert [token for token, _, _ in options.targets] == ["cat/a"]

    def test_invalid_atom(self, capsys, bugs_options):
        with pytest.raises(SystemExit):
            bugs_options("cat/a[")
        _out, err = capsys.readouterr()
        assert "pkgdev bugs: error" in err

    def test_explicit_arches_are_kept(self, repo):
        repo.create_ebuild("cat/a-1", KEYWORDS=["~amd64"])
        repo.sync()
        graph = mk_graph(repo)
        target = parserestrict.parse_match("=cat/a-1")
        graph.load_targets([("=cat/a-1", target, frozenset({"amd64"}))])
        (pkg,) = graph.targets
        assert graph.target_arches == {pkg: frozenset({"amd64"})}

    def test_masked_target_is_skipped(self, repo):
        repo.create_ebuild("cat/a-1", KEYWORDS=["~amd64"])
        with open(pjoin(repo.location, "profiles", "package.mask"), "w") as f:
            f.write("cat/a\n")
        repo.sync()
        graph = mk_graph(repo)
        graph.load_targets([("cat/a", parserestrict.parse_match("cat/a"), frozenset())])
        assert graph.targets == ()
        assert "is masked, skipping" in "".join(graph.err.lines)

    def test_target_without_a_match_errors(self, repo, capsys):
        graph = mk_graph(repo)
        target = parserestrict.parse_match("cat/nonexistent")
        with pytest.raises(SystemExit) as excinfo:
            graph.load_targets([("cat/nonexistent", target, frozenset())])
        assert excinfo.value.code == 3
        assert "has no match in repository" in capsys.readouterr().err

    def test_maintainer_packages_are_collected(self, repo, real_graph):
        mk_repo(repo)
        graph = real_graph("--find-by-maintainer", "dev1")
        found = {str(restrict) for _, restrict, _ in graph.extend_maintainers()}
        assert found == {"cat/u", "cat/y"}

    def test_disabled_maintainer_is_dropped(self, repo, real_graph):
        mk_repo(repo)
        graph = real_graph("--find-by-maintainer", "dev1", "--find-by-maintainer", "-dev1")
        assert list(graph.extend_maintainers()) == []

    def test_stabilization_group_packages_are_collected(self, repo, real_graph):
        repo.create_ebuild("cat/a-1", KEYWORDS=["~amd64"])
        repo.create_ebuild("cat/b-1", KEYWORDS=["~amd64"])
        mk_group(repo, "grp", "cat/a", "cat/b")
        graph = real_graph()
        found = {str(restrict) for _, restrict, _ in graph.extend_targets_stable_groups(["grp"])}
        assert found == {"cat/a", "cat/b"}


class TestFilterStablereqs:
    """A target matching many packages has a stablereq per package, not one."""

    def mk_stablereq_graph(self, repo, wanted):
        graph = mk_graph(repo)
        graph.options.filter_stablereqs = True
        fed = []

        class Check:
            def feed(self, pkgs):
                fed.append(tuple({pkg.key for pkg in pkgs}))
                for pkg in reversed(pkgs):
                    if pkg.versioned_atom.cpvstr in wanted:
                        yield bugs.stablereq.StableRequest(
                            slot=pkg.slot, keywords=pkg.keywords, age=40, pkg=pkg
                        )

        graph.stablereq_check = Check()
        return graph, fed

    def test_every_package_in_the_target_is_checked(self, repo):
        for name in ("a", "b", "c"):
            repo.create_ebuild(f"cat/{name}-1", KEYWORDS=["amd64"])
            repo.create_ebuild(f"cat/{name}-2", KEYWORDS=["~amd64"])
        repo.sync()
        graph, fed = self.mk_stablereq_graph(repo, {"cat/a-2", "cat/c-2"})

        graph.load_targets([(None, parserestrict.parse_match("cat/*"), frozenset())])

        assert sorted(str(pkg.versioned_atom) for pkg in graph.targets) == ["=cat/a-2", "=cat/c-2"]
        # one package per feed, as the check reads a single package's versions
        assert fed and all(len(keys) == 1 for keys in fed)

    def test_target_without_any_stablereq_is_dropped(self, repo):
        repo.create_ebuild("cat/a-1", KEYWORDS=["amd64"])
        repo.sync()
        graph, _ = self.mk_stablereq_graph(repo, set())
        graph.load_targets([(None, parserestrict.parse_match("cat/*"), frozenset())])
        assert graph.targets == ()

    def test_single_package_target_is_unchanged(self, repo):
        repo.create_ebuild("cat/a-1", KEYWORDS=["amd64"])
        repo.create_ebuild("cat/a-2", KEYWORDS=["~amd64"])
        repo.sync()
        graph, _ = self.mk_stablereq_graph(repo, {"cat/a-2"})
        graph.load_targets([(None, parserestrict.parse_match("cat/a"), frozenset())])
        assert [str(pkg.versioned_atom) for pkg in graph.targets] == ["=cat/a-2"]


STABLE_THEN_UNSTABLE = {
    "1": {"KEYWORDS": ["amd64"]},
    "2": {"KEYWORDS": ["~amd64"]},
    "3": {"KEYWORDS": ["~amd64"]},
}


class TestFindBestMatch:
    """Each step of the preference chain deciding which version gets filed."""

    def mk(self, repo, versions=None, masks=(), due=()):
        for ver, kwargs in (versions or STABLE_THEN_UNSTABLE).items():
            repo.create_ebuild(f"cat/dep-{ver}", **kwargs)
        if masks:
            with open(pjoin(repo.location, "profiles", "package.mask"), "w") as f:
                f.write("\n".join(masks) + "\n")
        repo.sync()

        graph = mk_graph(repo)
        if due:

            def feed(pkgs):
                for pkg in pkgs:
                    if pkg.versioned_atom.cpvstr in due:
                        yield bugs.stablereq.StableRequest(
                            slot=pkg.slot, keywords=pkg.keywords, age=40, pkg=pkg
                        )

            graph.stablereq_check = SimpleNamespace(feed=feed)
            graph.options.filter_stablereqs = True
        return graph, repo.match(atom("cat/dep"))

    @staticmethod
    def pick(graph, pkgset, **kwargs):
        return str(graph.find_best_match([atom("cat/dep")], pkgset, **kwargs).versioned_atom)

    @staticmethod
    def version(pkgset, fullver):
        return next(pkg for pkg in pkgset if pkg.fullver == fullver)

    def test_a_user_selected_target_wins(self, repo):
        graph, pkgset = self.mk(repo)
        graph.targets = (self.version(pkgset, "2"),)
        assert self.pick(graph, pkgset) == "=cat/dep-2"

    def test_the_newest_user_selected_target_wins(self, repo):
        graph, pkgset = self.mk(repo)
        graph.targets = (self.version(pkgset, "1"), self.version(pkgset, "2"))
        assert self.pick(graph, pkgset) == "=cat/dep-2"

    def test_a_package_already_in_the_graph_wins(self, repo):
        graph, pkgset = self.mk(repo)
        graph.nodes = {bugs.GraphNode(((self.version(pkgset, "2"), {"*"}),))}
        assert self.pick(graph, pkgset) == "=cat/dep-2"

    def test_a_user_target_beats_the_graph(self, repo):
        graph, pkgset = self.mk(repo)
        graph.targets = (self.version(pkgset, "1"),)
        graph.nodes = {bugs.GraphNode(((self.version(pkgset, "3"), {"*"}),))}
        assert self.pick(graph, pkgset) == "=cat/dep-1"

    def test_the_stablereq_due_version_wins(self, repo):
        graph, pkgset = self.mk(repo, due={"cat/dep-2"})
        assert self.pick(graph, pkgset) == "=cat/dep-2"

    def test_the_graph_beats_the_stablereq_due_version(self, repo):
        graph, pkgset = self.mk(repo, due={"cat/dep-2"})
        graph.nodes = {bugs.GraphNode(((self.version(pkgset, "3"), {"*"}),))}
        assert self.pick(graph, pkgset) == "=cat/dep-3"

    def test_the_stablereq_step_needs_filter_stablereqs(self, repo):
        graph, pkgset = self.mk(repo, due={"cat/dep-2"})
        graph.options.filter_stablereqs = False
        assert self.pick(graph, pkgset) == "=cat/dep-1"

    def test_a_due_version_outside_the_candidates_is_passed_over(self, repo):
        graph, pkgset = self.mk(repo, due={"cat/dep-3"})
        candidates = [pkg for pkg in pkgset if pkg.fullver != "3"]
        assert self.pick(graph, candidates) == "=cat/dep-1"

    def test_the_stablereq_answer_is_cached_per_package(self, repo):
        graph, pkgset = self.mk(repo, due={"cat/dep-2"})
        calls = []
        inner = graph.stablereq_check.feed
        graph.stablereq_check = SimpleNamespace(feed=lambda pkgs: (calls.append(1), inner(pkgs))[1])
        self.pick(graph, pkgset)
        self.pick(graph, pkgset)
        assert len(calls) == 1

    def test_a_semi_stable_version_beats_a_newer_unstable_one(self, repo):
        graph, pkgset = self.mk(repo)
        assert self.pick(graph, pkgset) == "=cat/dep-1"

    def test_the_newest_wins_when_semi_stable_is_not_preferred(self, repo):
        graph, pkgset = self.mk(repo)
        assert self.pick(graph, pkgset, prefer_semi_stable=False) == "=cat/dep-3"

    def test_versions_without_keywords_are_passed_over(self, repo):
        versions = dict(STABLE_THEN_UNSTABLE, **{"3": {"KEYWORDS": []}})
        graph, pkgset = self.mk(repo, versions)
        assert self.pick(graph, pkgset, prefer_semi_stable=False) == "=cat/dep-2"

    def test_the_newest_is_the_last_resort(self, repo):
        versions = {ver: {"KEYWORDS": []} for ver in ("1", "2", "3")}
        graph, pkgset = self.mk(repo, versions)
        assert self.pick(graph, pkgset) == "=cat/dep-3"

    def test_live_versions_are_excluded(self, repo):
        # keyworded, so only the live filter can keep it out of the running
        versions = dict(
            STABLE_THEN_UNSTABLE, **{"9999": {"PROPERTIES": "live", "KEYWORDS": ["~amd64"]}}
        )
        graph, pkgset = self.mk(repo, versions)
        assert self.pick(graph, pkgset, prefer_semi_stable=False) == "=cat/dep-3"

    def test_masked_versions_are_excluded(self, repo):
        graph, pkgset = self.mk(repo, masks=("=cat/dep-3",))
        assert self.pick(graph, pkgset, prefer_semi_stable=False) == "=cat/dep-2"


class TestSettledVersions:
    def _mk_pkgs(self, repo, *keywords):
        for i, kws in enumerate(keywords, start=1):
            repo.create_ebuild(f"cat/a-{i}", KEYWORDS=kws)
        return sorted(repo.itermatch(atom("cat/a")))

    def test_stable_versions_are_dropped(self, repo):
        # a-1 is already stable on amd64, so stabilizing it again solves nothing
        pkgs = self._mk_pkgs(repo, ["amd64"], ["~amd64"])
        assert bugs.DependencyGraph._drop_settled(pkgs, {"amd64"}, True) == pkgs[1:]

    def test_keyworded_versions_are_dropped_for_keywordreqs(self, repo):
        # keywording only makes sense where the arch is missing entirely
        pkgs = self._mk_pkgs(repo, ["~amd64"], ["amd64"], ["~x86"])
        assert bugs.DependencyGraph._drop_settled(pkgs, {"amd64"}, False) == pkgs[2:]
        # a stablereq still wants the ~amd64 one
        assert bugs.DependencyGraph._drop_settled(pkgs, {"amd64"}, True) == [pkgs[0], pkgs[2]]

    def test_settled_on_any_arch_is_dropped(self, repo):
        # the same version has to answer every arch it was reported for
        pkgs = self._mk_pkgs(repo, ["amd64", "~x86"], ["~amd64", "~x86"])
        assert bugs.DependencyGraph._drop_settled(pkgs, {"amd64", "x86"}, True) == pkgs[1:]

    def test_all_settled_keeps_the_full_set(self, repo):
        # the answer is a version that doesn't exist yet, leave the error to the caller
        pkgs = self._mk_pkgs(repo, ["amd64"], ["amd64"])
        assert bugs.DependencyGraph._drop_settled(pkgs, {"amd64"}, True) == pkgs


class TestSuggestedKeywords:
    """pkgcore owns the rule; these pin the semantics pkgdev relies on."""

    def test_stablereq(self, repo):
        repo.create_ebuild("cat/a-1", KEYWORDS=["amd64", "x86"])
        repo.create_ebuild("cat/a-2", KEYWORDS=["amd64", "~x86"])
        pkg = max(repo.itermatch(atom("=cat/a-2")))
        # only ~arch keywords here that are stable on another version may be stabilized
        assert bugs.suggested_keywords(repo, pkg, stable=True) == frozenset({"x86"})

    def test_keywordreq(self, repo):
        repo.create_ebuild("cat/a-1", KEYWORDS=["~amd64", "~x86"])
        repo.create_ebuild("cat/a-2", KEYWORDS=["~amd64"])
        pkg = max(repo.itermatch(atom("=cat/a-2")))
        # keywords present on other versions but missing here are suggested
        assert bugs.suggested_keywords(repo, pkg, stable=False) == frozenset({"x86"})


class TestAnyOfDependencies:
    def test_flat_groups_are_found(self, repo):
        repo.create_ebuild("cat/parent-1", RDEPEND="|| ( cat/a cat/b )", DEPEND="cat/c")
        parent = max(repo.itermatch(atom("=cat/parent-1")))
        assert bugs.DependencyGraph._any_of_groups(parent, "rdepend") == (
            (atom("cat/a"), atom("cat/b")),
        )
        assert bugs.DependencyGraph._any_of_groups(parent, "depend") == ()

    def test_groups_inside_use_conditionals(self, repo):
        repo.create_ebuild("cat/parent-1", IUSE="foo", RDEPEND="foo? ( || ( cat/a cat/b ) )")
        parent = max(repo.itermatch(atom("=cat/parent-1")))
        assert bugs.DependencyGraph._any_of_groups(parent, "rdepend") == (
            (atom("cat/a"), atom("cat/b")),
        )

    def test_nested_alternatives_are_skipped(self, repo):
        # picking one atom out of "( cat/a cat/b )" would drop the other, which
        # the alternative needs, so the block is left alone
        repo.create_ebuild("cat/parent-1", RDEPEND="|| ( ( cat/a cat/b ) cat/c )")
        parent = max(repo.itermatch(atom("=cat/parent-1")))
        assert bugs.DependencyGraph._any_of_groups(parent, "rdepend") == ()

    def _mk_alternatives(self, repo, *, keywords=("~amd64", "~amd64", "~amd64")):
        for name, kws in zip("abc", keywords):
            repo.create_ebuild(f"cat/{name}-1", KEYWORDS=[kws])
        return ((atom("cat/a"), atom("cat/b"), atom("cat/c")),)

    def test_selected_alternative_wins(self, repo):
        # cat/b is already being handled, so nothing else has to be
        groups = self._mk_alternatives(repo)
        graph = mk_graph(repo)
        graph.targets = (max(repo.itermatch(atom("=cat/b-1"))),)
        deps = {atom("cat/a"), atom("cat/b"), atom("cat/c")}
        assert graph._pick_alternatives(groups, "amd64", deps) == {atom("cat/b")}

    def test_alternative_in_the_graph_wins(self, repo):
        groups = self._mk_alternatives(repo)
        graph = mk_graph(repo)
        c = max(repo.itermatch(atom("=cat/c-1")))
        graph.nodes = {bugs.GraphNode(((c, {"amd64"}),))}
        deps = {atom("cat/a"), atom("cat/b"), atom("cat/c")}
        assert graph._pick_alternatives(groups, "amd64", deps) == {atom("cat/c")}

    def test_keyworded_alternative_beats_one_needing_a_keywordreq(self, repo):
        # cat/a would have to be keyworded on amd64 first, cat/b wouldn't
        groups = self._mk_alternatives(repo, keywords=("~x86", "~amd64", "~x86"))
        graph = mk_graph(repo)
        deps = {atom("cat/a"), atom("cat/b"), atom("cat/c")}
        assert graph._pick_alternatives(groups, "amd64", deps) == {atom("cat/b")}

    def test_ebuild_order_breaks_ties(self, repo):
        # nothing to prefer, so the ebuild's own first choice is taken
        groups = self._mk_alternatives(repo)
        graph = mk_graph(repo)
        deps = {atom("cat/a"), atom("cat/b"), atom("cat/c")}
        assert graph._pick_alternatives(groups, "amd64", deps) == {atom("cat/a")}

    def test_alternative_without_a_match_is_last(self, repo):
        # cat/a is gone from the repo, so it can never solve the block
        repo.create_ebuild("cat/b-1", KEYWORDS=["~amd64"])
        repo.create_ebuild("cat/c-1", KEYWORDS=["~amd64"])
        groups = ((atom("cat/a"), atom("cat/b"), atom("cat/c")),)
        graph = mk_graph(repo)
        deps = {atom("cat/a"), atom("cat/b"), atom("cat/c")}
        assert graph._pick_alternatives(groups, "amd64", deps) == {atom("cat/b")}

    def test_deps_outside_a_group_are_kept(self, repo):
        # all-of failures are each mandatory, and a lone alternative isn't a choice
        groups = self._mk_alternatives(repo)
        graph = mk_graph(repo)
        deps = {atom("cat/a"), atom("cat/d")}
        assert graph._pick_alternatives(groups, "amd64", deps) == deps


class TestFindDependencies:
    """Dependency discovery run for real, against profiles and the visibility check."""

    def test_unsolvable_dep_is_reported(self, repo, real_graph):
        mk_profiles(repo, "amd64")
        repo.create_ebuild("cat/dep-1", KEYWORDS=["~amd64"])
        repo.create_ebuild("cat/a-1", KEYWORDS=["~amd64"], RDEPEND="cat/dep")
        repo.sync()
        graph = real_graph()
        pkg = max(repo.itermatch(atom("=cat/a-1")))
        assert [
            (str(dep.versioned_atom), arches)
            for dep, arches in graph._find_dependencies(pkg, {"amd64"})
        ] == [("=cat/dep-1", {"amd64"})]

    def test_stable_dep_is_not_reported(self, repo, real_graph):
        mk_profiles(repo, "amd64")
        repo.create_ebuild("cat/dep-1", KEYWORDS=["amd64"])
        repo.create_ebuild("cat/a-1", KEYWORDS=["~amd64"], RDEPEND="cat/dep")
        repo.sync()
        graph = real_graph()
        pkg = max(repo.itermatch(atom("=cat/a-1")))
        assert list(graph._find_dependencies(pkg, {"amd64"})) == []

    def test_any_of_block_yields_one_alternative(self, repo, real_graph):
        mk_profiles(repo, "amd64")
        repo.create_ebuild("cat/a-1", KEYWORDS=["~amd64"])
        repo.create_ebuild("cat/b-1", KEYWORDS=["~amd64"])
        repo.create_ebuild("cat/p-1", KEYWORDS=["~amd64"], RDEPEND="|| ( cat/a cat/b )")
        repo.sync()
        graph = real_graph()
        pkg = max(repo.itermatch(atom("=cat/p-1")))
        deps = [str(dep.versioned_atom) for dep, _ in graph._find_dependencies(pkg, {"amd64"})]
        assert deps == ["=cat/a-1"]

    def test_dep_with_no_version_to_pick_errors(self, repo, real_graph, capsys):
        mk_profiles(repo, "amd64")
        repo.create_ebuild("cat/dep-1", KEYWORDS=["~amd64"])
        repo.create_ebuild("cat/a-1", KEYWORDS=["~amd64"], RDEPEND=">=cat/dep-2")
        repo.sync()
        graph = real_graph()
        pkg = max(repo.itermatch(atom("=cat/a-1")))
        with pytest.raises(SystemExit) as excinfo:
            list(graph._find_dependencies(pkg, {"amd64"}))
        assert excinfo.value.code == 3
        assert "unable to find match for restrictions" in capsys.readouterr().err

    def test_own_slot_is_not_a_dependency(self, repo, real_graph):
        # rust's bootstrap block lists its own slot, which this very bug solves
        mk_profiles(repo, "amd64")
        repo.create_ebuild("cat/rust-1", KEYWORDS=["~amd64"], slot="1")
        repo.create_ebuild(
            "cat/rust-2", KEYWORDS=["~amd64"], slot="2", RDEPEND="|| ( cat/rust:2 cat/rust:1 )"
        )
        repo.sync()
        graph = real_graph()
        pkg = max(repo.itermatch(atom("=cat/rust-2")))
        assert list(graph._find_dependencies(pkg, {"amd64"})) == []


class TestBuildGraph:
    def test_stable_to_keyword_chain(self, repo):
        # the dependency has no amd64 keyword at all -> it must be keyworded before
        # it can be stabilized, producing a parent-stable -> dep-stable -> dep-keyword chain
        repo.create_ebuild("cat/parent-1", KEYWORDS=["amd64"])
        repo.create_ebuild("cat/parent-2", KEYWORDS=["~amd64"])
        repo.create_ebuild("cat/dep-1", KEYWORDS=["~x86"])
        parent = max(repo.itermatch(atom("=cat/parent-2")))
        dep = max(repo.itermatch(atom("=cat/dep-1")))

        graph = mk_graph(repo)
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

        graph = mk_graph(repo)
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

    def test_stable_dep_already_keyworded_no_chain(self, repo):
        # the dependency is ~amd64, so it can be stabilized directly without a keyword bug
        repo.create_ebuild("cat/parent-1", KEYWORDS=["amd64"])
        repo.create_ebuild("cat/parent-2", KEYWORDS=["~amd64"])
        repo.create_ebuild("cat/dep-1", KEYWORDS=["~amd64"])
        parent = max(repo.itermatch(atom("=cat/parent-2")))
        dep = max(repo.itermatch(atom("=cat/dep-1")))

        graph = mk_graph(repo)
        graph.targets = (parent,)

        def fake_find_dependencies(pkg, keywords, stable=True):
            if pkg == parent:
                yield dep, {"amd64"}

        graph._find_dependencies = fake_find_dependencies
        graph.build_full_graph()

        # only stablereq nodes, no keywordreq node
        assert all(not node.is_keywordreq for node in graph.nodes)
        assert len(graph.nodes) == 2

    def test_nothing_to_stable_is_skipped(self, repo):
        # already stable everywhere it is keyworded
        repo.create_ebuild("cat/a-1", KEYWORDS=["amd64"])
        pkg = max(repo.itermatch(atom("=cat/a-1")))
        graph = mk_graph(repo)
        graph.targets = (pkg,)
        graph._find_dependencies = lambda *a, **k: iter(())
        graph.build_full_graph()
        assert graph.nodes == set()
        assert "Nothing to stable for cat/a" in "".join(graph.out.lines)


class TestMaskedKeywords:
    def test_keyword_target_without_arches_errors(self, repo):
        # a keyword target with no other versions to derive arches from must error
        repo.create_ebuild("cat/a-1", KEYWORDS=["~amd64"])
        pkg = max(repo.itermatch(atom("=cat/a-1")))
        graph = mk_graph(repo, category=BugCategory.KEYWORDREQ)
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
        graph = mk_graph(repo, category=BugCategory.KEYWORDREQ)
        graph.targets = (pkg,)
        graph.target_arches = {pkg: frozenset({"loong"})}
        graph._find_dependencies = lambda *a, **k: iter(())
        with pytest.raises(SystemExit):
            graph.build_full_graph()


class TestMergeMatchedBugs:
    """Several nodes matching one existing bug must not make it depend on itself."""

    def mk_shared(self, repo):
        """A dependency path whose ends matched the same existing bug."""
        pkg = max(repo.itermatch(atom("=cat/u-0")))
        graph = mk_graph(repo)
        top = bugs.GraphNode((), bugno=597)
        middle = bugs.GraphNode(((pkg, {"*"}),))
        bottom = bugs.GraphNode((), bugno=597)
        top.edges.add(middle)
        middle.edges.add(bottom)
        graph.nodes.update((top, middle, bottom))
        graph.starting_nodes.add(top)
        return graph, middle

    def test_shared_bug_is_merged(self, repo):
        mk_repo(repo)
        graph, middle = self.mk_shared(repo)
        graph.merge_matched_bugs()

        merged = [node for node in graph.nodes if node.bugno == 597]
        assert len(merged) == 1, "nodes sharing a bug must become one node"
        # the path collapsed into a cycle, which merge_cycles then folds together
        assert merged[0].edges == {middle}
        assert middle.edges == {merged[0]}

    def test_bug_in_use_is_not_obsoleted(self, repo):
        mk_repo(repo)
        graph, middle = self.mk_shared(repo)
        middle.obsoletes.add(597)
        graph.merge_matched_bugs()

        assert not any(node.obsoletes for node in graph.nodes), (
            "a bug kept as another node's bug must not also be resolved obsolete"
        )

    def test_merging_different_bugs_errors(self, repo, capsys):
        mk_repo(repo)
        graph = mk_graph(repo)
        nodes = (bugs.GraphNode((), bugno=1), bugs.GraphNode((), bugno=2))
        graph.nodes.update(nodes)
        with pytest.raises(SystemExit) as excinfo:
            graph.merge_nodes(nodes)
        assert excinfo.value.code == 3
        assert "different existing bugs" in capsys.readouterr().err

    def test_merge_nodes_keeps_obsoletes(self, repo):
        repo.create_ebuild("cat/a-1", KEYWORDS=["~amd64"])
        repo.create_ebuild("cat/b-1", KEYWORDS=["~amd64"])
        a = max(repo.itermatch(atom("=cat/a-1")))
        b = max(repo.itermatch(atom("=cat/b-1")))

        graph = mk_graph(repo)
        first = bugs.GraphNode(((a, {"amd64"}),))
        first.obsoletes.add(100)
        second = bugs.GraphNode(((b, {"amd64"}),))
        second.obsoletes.add(101)
        graph.nodes = {first, second}

        merged = graph.merge_nodes((first, second))
        assert merged.obsoletes == {100, 101}


class TestMergeCycles:
    def test_cycle_is_folded_into_one_node(self, repo):
        mk_repo(repo)
        graph, _ = TestMergeMatchedBugs().mk_shared(repo)
        graph.merge_matched_bugs()
        graph.merge_cycles()
        assert len(graph.nodes) == 1
        assert next(iter(graph.nodes)).bugno == 597, "the existing bug must be kept"

    def test_no_self_dependency_is_filed(self, repo, bugzilla_cassette):
        mk_repo(repo)
        graph, _ = TestMergeMatchedBugs().mk_shared(repo)
        graph.merge_matched_bugs()
        graph.merge_cycles()

        node = next(iter(graph.nodes))
        node.file_bug(bugzilla_cassette.client(api_key="API"), frozenset(), (), None)
        # nothing to file: the existing bug covers the whole cycle, and 597 was
        # never made to depend on a bug which depends on it
        assert bugzilla_cassette.calls == []

    def test_mixed_category_cycle_errors(self, repo, capsys):
        repo.create_ebuild("cat/a-1", KEYWORDS=["~amd64"])
        pkg = max(repo.itermatch(atom("=cat/a-1")))
        graph = mk_graph(repo)
        stable = bugs.GraphNode(((pkg, {"amd64"}),))
        keyword = bugs.GraphNode(((pkg, {"amd64"}),), category=BugCategory.KEYWORDREQ)
        stable.edges.add(keyword)
        keyword.edges.add(stable)
        graph.nodes = {stable, keyword}
        graph.starting_nodes = {stable}
        with pytest.raises(SystemExit) as excinfo:
            graph.merge_cycles()
        assert excinfo.value.code == 3
        assert "cannot be merged" in capsys.readouterr().err


class TestMergeStabilizationGroups:
    def mk_group_graph(self, repo, *pkgs):
        for name in pkgs:
            repo.create_ebuild(f"{name}-1", KEYWORDS=["~amd64"])
        mk_group(repo, "grp", *pkgs)
        graph = mk_graph(repo)
        graph.nodes = {
            bugs.GraphNode(((max(repo.itermatch(atom(f"={name}-1"))), {"amd64"}),)) for name in pkgs
        }
        return graph

    def test_group_nodes_are_merged(self, repo, monkeypatch):
        set_userquery(monkeypatch)
        graph = self.mk_group_graph(repo, "cat/a", "cat/b")
        assert graph.merge_stabilization_groups(graph.out, graph.err)
        (node,) = graph.nodes
        assert {pkg.cpvstr for pkg, _ in node.pkgs} == {"cat/a-1", "cat/b-1"}

    def test_missing_group_package_is_confirmed(self, repo, monkeypatch):
        set_userquery(monkeypatch)
        graph = self.mk_group_graph(repo, "cat/a", "cat/b")
        # drop cat/b from the graph, keeping it in the group
        graph.nodes = {node for node in graph.nodes if node.pkgs[0][0].key != "cat/b"}
        assert graph.merge_stabilization_groups(graph.out, graph.err)
        assert "Detected 1 missing packages in @grp group" in "".join(graph.out.lines)

    def test_missing_group_package_aborts(self, repo, monkeypatch):
        set_userquery(monkeypatch, yesno=False)
        graph = self.mk_group_graph(repo, "cat/a", "cat/b")
        graph.nodes = {node for node in graph.nodes if node.pkgs[0][0].key != "cat/b"}
        assert not graph.merge_stabilization_groups(graph.out, graph.err)

    def test_matched_bugs_are_left_alone(self, repo, monkeypatch):
        set_userquery(monkeypatch)
        graph = self.mk_group_graph(repo, "cat/a", "cat/b")
        for node in graph.nodes:
            node.bugno = 100
        graph.merge_stabilization_groups(graph.out, graph.err)
        assert len(graph.nodes) == 2


class TestMergeNewKeywordsChildren:
    def mk_parent_child(self, repo, child_keywords, category=BugCategory.KEYWORDREQ):
        repo.create_ebuild("cat/parent-1", KEYWORDS=["~amd64"])
        repo.create_ebuild("cat/child-1", KEYWORDS=child_keywords)
        repo.sync()
        parent = max(repo.itermatch(atom("=cat/parent-1")))
        child = max(repo.itermatch(atom("=cat/child-1")))

        graph = mk_graph(repo)
        parent_node = bugs.GraphNode(((parent, {"amd64"}),), category=category)
        child_node = bugs.GraphNode(((child, {"amd64"}),), category=BugCategory.KEYWORDREQ)
        parent_node.edges.add(child_node)
        graph.nodes = {parent_node, child_node}
        return graph, parent_node, child_node

    def test_single_parent_child_is_merged(self, repo):
        graph, _parent, _child = self.mk_parent_child(repo, ["~x86"])
        graph.merge_new_keywords_children()
        (node,) = graph.nodes
        assert {pkg.cpvstr for pkg, _ in node.pkgs} == {"cat/parent-1", "cat/child-1"}

    def test_child_with_the_keyword_is_kept(self, repo):
        # cat/child-1 already carries amd64, so it isn't a fully new keyword
        graph, _parent, _child = self.mk_parent_child(repo, ["amd64"])
        graph.merge_new_keywords_children()
        assert len(graph.nodes) == 2

    def test_keywordreq_child_is_not_merged_into_a_stablereq(self, repo):
        # merging would put ~arch keywords into a Stabilization bug
        graph, _parent, _child = self.mk_parent_child(
            repo, ["~x86"], category=BugCategory.STABLEREQ
        )
        graph.merge_new_keywords_children()
        assert len(graph.nodes) == 2

    def test_child_of_two_parents_is_kept(self, repo):
        graph, _parent, child = self.mk_parent_child(repo, ["~x86"])
        other = bugs.GraphNode((), category=BugCategory.KEYWORDREQ)
        other.edges.add(child)
        graph.nodes.add(other)
        graph.merge_new_keywords_children()
        assert child in graph.nodes

    def test_child_with_a_filed_bug_is_kept(self, repo):
        graph, _parent, child = self.mk_parent_child(repo, ["~x86"])
        child.bugno = 100
        graph.merge_new_keywords_children()
        assert len(graph.nodes) == 2


class TestScanExistingBugs:
    def mk_bug_graph(self, repo, *nodes):
        graph = mk_graph(repo)
        graph.nodes = set(nodes)
        graph.starting_nodes = set(nodes)
        return graph

    def mk_node(self, repo, cpvstr="cat/a-1"):
        repo.create_ebuild(cpvstr, KEYWORDS=["~amd64"])
        return bugs.GraphNode(((max(repo.itermatch(atom(f"={cpvstr}"))), {"amd64"}),))

    @pytest.mark.parametrize("reverse", (False, True))
    def test_obsolete_older_bug_with_exact_match(
        self, repo, bugzilla_cassette, monkeypatch, reverse
    ):
        # an old atom match and a new exact match for the same node must obsolete
        # the old one, no matter which order bugzilla returns them in
        set_userquery(monkeypatch, choice="obsolete")
        node = self.mk_node(repo)
        graph = self.mk_bug_graph(repo, node)

        found = [mk_bug(100, "=cat/a-0 amd64"), mk_bug(200, "=cat/a-1 amd64")]
        bugzilla_cassette.expect_bugs(*reversed(found) if reverse else found)
        assert graph.scan_existing_bugs(bugzilla_cassette.client())
        assert node.bugno == 200
        assert node.obsoletes == {100}

    def test_atom_match_can_be_reused(self, repo, bugzilla_cassette, monkeypatch):
        set_userquery(monkeypatch, choice="reuse")
        node = self.mk_node(repo)
        graph = self.mk_bug_graph(repo, node)

        bugzilla_cassette.expect_bugs(mk_bug(100, "=cat/a-0 amd64"))
        assert graph.scan_existing_bugs(bugzilla_cassette.client())
        assert node.bugno == 100
        assert node.obsoletes == set()

    def test_atom_match_can_be_left_alone(self, repo, bugzilla_cassette, monkeypatch):
        # answering "new" files our own bug and leaves the matched one untouched
        set_userquery(monkeypatch, choice="new")
        node = self.mk_node(repo)
        graph = self.mk_bug_graph(repo, node)

        bugzilla_cassette.expect_bugs(mk_bug(100, "=cat/a-0 amd64"))
        assert graph.scan_existing_bugs(bugzilla_cassette.client())
        assert node.bugno is None
        assert node.obsoletes == set()

    def test_keywordreq_bug_is_not_matched_to_a_stablereq(self, repo, bugzilla_cassette):
        node = self.mk_node(repo)
        graph = self.mk_bug_graph(repo, node)

        bugzilla_cassette.expect_bugs(mk_bug(100, "=cat/a-1 amd64", component="Keywording"))
        assert not graph.scan_existing_bugs(bugzilla_cassette.client())
        assert node.bugno is None


class TestCleanupKeywords:
    def test_full_keyword_set_collapses_to_star(self, repo):
        repo.create_ebuild("cat/a-1", KEYWORDS=["amd64", "x86"])
        repo.create_ebuild("cat/a-2", KEYWORDS=["~amd64", "~x86"])
        pkg = max(repo.itermatch(atom("=cat/a-2")))
        node = bugs.GraphNode(((pkg, {"amd64", "x86"}),))
        node.cleanup_keywords(repo)
        assert [keywords for _, keywords in node.pkgs] == [{"*"}]

    def test_repeated_keywords_collapse_to_caret(self, repo):
        repo.create_ebuild("cat/a-1", KEYWORDS=["~amd64"])
        repo.create_ebuild("cat/b-1", KEYWORDS=["~amd64"])
        a = max(repo.itermatch(atom("=cat/a-1")))
        b = max(repo.itermatch(atom("=cat/b-1")))
        node = bugs.GraphNode(((a, {"amd64"}), (b, {"amd64"})))
        node.cleanup_keywords(repo)
        assert [keywords for _, keywords in node.pkgs] == [{"amd64"}, {"^"}]


class TestGraphToml:
    def test_load_graph_toml_category(self, repo, tmp_path):
        repo.create_ebuild("cat/a-1", KEYWORDS=["~amd64"])
        graph = mk_graph(repo)
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

    def test_unknown_dependency_errors(self, repo, tmp_path):
        repo.create_ebuild("cat/a-1", KEYWORDS=["~amd64"])
        graph = mk_graph(repo)
        toml_file = tmp_path / "graph.toml"
        toml_file.write_text(
            textwrap.dedent(
                """\
                [bug-1]
                "=cat/a-1" = ["amd64"]
                depends = ["bug-9"]
                """
            )
        )
        with pytest.raises(ValueError, match="unknown dependency"):
            graph.load_graph_toml(str(toml_file))

    def test_roundtrip_preserves_starting_node(self, repo):
        # regression test for https://github.com/pkgcore/pkgdev/issues/218 :
        # a target node with an already-filed dependency must remain a starting
        # node after an output_graph_toml() -> load_graph_toml() round trip
        repo.create_ebuild("cat/a-1", KEYWORDS=["~amd64"])
        repo.create_ebuild("cat/b-1", KEYWORDS=["~amd64"])
        a = max(repo.itermatch(atom("=cat/a-1")))
        b = max(repo.itermatch(atom("=cat/b-1")))

        graph = mk_graph(repo)
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

    def test_output_dot(self, repo, tmp_path):
        repo.create_ebuild("cat/a-1", KEYWORDS=["~amd64"])
        repo.create_ebuild("cat/b-1", KEYWORDS=["~amd64"])
        a = max(repo.itermatch(atom("=cat/a-1")))
        b = max(repo.itermatch(atom("=cat/b-1")))

        graph = mk_graph(repo)
        dep = bugs.GraphNode(((b, {"amd64"}),), bugno=100)
        target = bugs.GraphNode(((a, {"amd64"}),))
        target.edges.add(dep)
        graph.nodes = {target, dep}

        dot_file = tmp_path / "graph.dot"
        graph.output_dot(str(dot_file))
        content = dot_file.read_text()
        assert content.startswith("digraph {")
        assert "cat/a-1" in content
        assert "bug #100" in content
        assert f"{target.dot_edge} -> {dep.dot_edge};" in content


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

    def test_missing_deps_of_existing_bug(self, repo, bugzilla_cassette):
        # a node matched to an existing bug must still file the dependency bugs
        # which weren't filed yet, and get linked against them
        mk_repo(repo)
        pkg = max(repo.itermatch(atom("=cat/u-0")))
        node = bugs.GraphNode((), bugno=200)
        dep = bugs.GraphNode(((pkg, {"*"}),))
        node.edges.add(dep)

        bugzilla_cassette.expect_created(300).expect_changed(200)
        assert node.file_bug(bugzilla_cassette.client(api_key="API"), frozenset(), (), None) == 200
        assert dep.bugno == 300

        assert len(bugzilla_cassette.calls) == 2
        update = bugzilla_cassette.calls[1]
        assert update.method == "PUT"
        assert update.body["ids"] == [200]
        assert update.body["depends_on"] == {"add": ["300"]}

    def test_obsoletion_of_existing_bug_is_filed(self, repo, bugzilla_cassette):
        # nothing new to file, but the matched bug still has to obsolete the old one
        repo.create_ebuild("cat/a-1", KEYWORDS=["~amd64"])
        pkg = max(repo.itermatch(atom("=cat/a-1")))

        graph = mk_graph(repo)
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

    def test_creation_error_names_the_node(self, repo, bugzilla_cassette):
        mk_repo(repo)
        pkg = max(repo.itermatch(atom("=cat/u-0")))
        node = bugs.GraphNode(((pkg, {"*"}),))
        bugzilla_cassette.expect_error(116, "circular dependency")
        with pytest.raises(BugzillaError) as excinfo:
            node.file_bug(bugzilla_cassette.client(api_key="API"), frozenset(), (), None)
        assert "filing bug for =cat/u-0" in str(excinfo.value)
        assert "circular dependency" in str(excinfo.value)

    def test_dependency_update_error_names_the_bug(self, repo, bugzilla_cassette):
        mk_repo(repo)
        pkg = max(repo.itermatch(atom("=cat/u-0")))
        node = bugs.GraphNode((), bugno=200)
        node.edges.add(bugs.GraphNode(((pkg, {"*"}),)))
        bugzilla_cassette.expect_created(300).expect_error(116, "circular dependency")
        with pytest.raises(BugzillaError) as excinfo:
            node.file_bug(bugzilla_cassette.client(api_key="API"), frozenset(), (), None)
        assert "adding dependencies to bug 200" in str(excinfo.value)


class TestMain:
    def mk_target(self, repo):
        mk_profiles(repo, "amd64")
        repo.create_ebuild("cat/a-1", KEYWORDS=["amd64"])
        repo.create_ebuild("cat/a-2", KEYWORDS=["~amd64"])
        repo.sync()

    def test_missing_api_key(self, repo, bugs_options):
        self.mk_target(repo)
        out = mk_out()
        assert bugs.main(bugs_options("=cat/a-2"), out, out) == 1
        assert "No API key provided" in "".join(out.lines)

    def test_nothing_to_do(self, repo, bugs_options):
        # cat/a-1 is already stable on every arch it is keyworded on
        self.mk_target(repo)
        out = mk_out()
        assert bugs.main(bugs_options("--api-key", "KEY", "=cat/a-1"), out, out) == 1
        assert "Nothing to do, exiting" in "".join(out.lines)

    def test_dot_file_is_written(self, repo, bugs_options, monkeypatch, tmp_path):
        self.mk_target(repo)
        set_userquery(monkeypatch, yesno=False)
        dot_file = tmp_path / "graph.dot"
        out = mk_out()
        options = bugs_options("--api-key", "KEY", "--dot", str(dot_file), "=cat/a-2")
        # answering no to the final prompt stops before filing
        assert bugs.main(options, out, out) == 1
        assert "cat/a-2" in dot_file.read_text()

    def test_bugs_are_filed(self, repo, bugs_options, bugzilla_cassette, monkeypatch):
        self.mk_target(repo)
        set_userquery(monkeypatch, yesno=True)
        bugzilla_cassette.expect_bugs().creates_bugs()
        out = mk_out()
        options = bugs_options("--api-key", "KEY", "=cat/a-2")
        bugs.main(options, out, out)

        create = bugzilla_cassette.calls[-1].body
        assert create["summary"] == "cat/a-2: stablereq"
        assert create["cf_stabilisation_atoms"] == "=cat/a-2 *"
