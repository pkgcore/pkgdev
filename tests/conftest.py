import pytest
from pkgdev.cli import Tool
from pkgdev.scripts import pkgdev
from snakeoil.cli import arghparse

pytest_plugins = ['pkgcore']


@pytest.fixture(scope="session")
def tool():
    """Generate a tool utility for running pkgdev."""
    return Tool(pkgdev.argparser)


@pytest.fixture
def parser():
    """Return a shallow copy of the main pkgdev argparser."""
    return pkgdev.argparser.copy()


@pytest.fixture
def namespace():
    """Return an arghparse Namespace object."""
    return arghparse.Namespace()
