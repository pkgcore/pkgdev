import os
import shutil
import tempfile

import pytest
from snakeoil.cli import arghparse

from pkgdev.cli import Tool
from pkgdev.scripts import pkgdev

pytest_plugins = ["pkgcore"]


@pytest.fixture(scope="session")
def temporary_home():
    """Generate a temporary directory and set$HOME to it."""
    old_home = os.environ.get("HOME")
    new_home = None
    try:
        new_home = tempfile.mkdtemp()
        os.environ["HOME"] = new_home
        yield
    finally:
        if old_home is None:
            del os.environ["HOME"]
        else:
            os.environ["HOME"] = old_home
            shutil.rmtree(new_home)  # pyright: ignore[reportArgumentType]


@pytest.fixture(scope="session")
def tool(temporary_home):
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
