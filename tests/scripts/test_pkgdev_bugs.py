import itertools
import os
import sys
import json
import textwrap
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from pkgcore.ebuild.atom import atom
from pkgcore.test.misc import FakePkg
from pkgdev.scripts import pkgdev_bugs as bugs
from snakeoil.formatters import PlainTextFormatter
from snakeoil.osutils import pjoin


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
                            {' '.join(f'<email>{maintainer}@gentoo.org</email>' for maintainer in maintainers)}
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
        dep = bugs.GraphNode((), 2)
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
