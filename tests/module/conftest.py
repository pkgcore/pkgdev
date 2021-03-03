import pytest
from pkgdev.cli import Tool
from pkgdev.scripts import pkgdev

pytest_plugins = ['pkgcore']


@pytest.fixture(scope="session")
def tool():
    """Generate a tool utility for running pkgdev."""
    tool = Tool(pkgdev.argparser)
    return tool


@pytest.fixture
def parser():
    """Return a shallow copy of the main pkgdev argparser."""
    return pkgdev.argparser.copy()
