|test| |coverage|

======
pkgdev
======

pkgdev provides a collection of tools for Gentoo development including:

**pkgdev commit**: commit to an ebuild repository

**pkgdev manifest**: update package manifests

**pkgdev push**: scan commits for QA issues before pushing upstream

Dependencies
============

pkgdev is developed alongside pkgcheck_, pkgcore_, and snakeoil_. Running
pkgdev from git will often require them from git as well.

For releases, see the required runtime dependencies_.

Installing
==========

Installing latest pypi release::

    pip install pkgdev

Installing from git::

    pip install https://github.com/pkgcore/pkgdev/archive/main.tar.gz

Installing from a tarball::

    python setup.py install

Tests
=====

A standalone test runner is integrated in setup.py::

    python setup.py test

In addition, a tox config is provided so the testsuite can be run in a
virtualenv setup against all supported python versions. To run tests for all
environments just execute **tox** in the root directory of a repo or unpacked
tarball. Otherwise, for a specific python version execute something similar to
the following::

    tox -e py39


.. _pkgcheck: https://github.com/pkgcore/pkgcheck
.. _pkgcore: https://github.com/pkgcore/pkgcore
.. _snakeoil: https://github.com/pkgcore/snakeoil
.. _dependencies: https://github.com/pkgcore/pkgdev/blob/main/requirements/install.txt

.. |test| image:: https://github.com/pkgcore/pkgdev/workflows/test/badge.svg
    :target: https://github.com/pkgcore/pkgdev/actions?query=workflow%3A%22test%22
.. |coverage| image:: https://codecov.io/gh/pkgcore/pkgdev/branch/main/graph/badge.svg
    :target: https://codecov.io/gh/pkgcore/pkgdev
