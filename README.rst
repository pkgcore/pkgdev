|pypi| |test| |coverage|

======
pkgdev
======

pkgdev provides a collection of tools for Gentoo development including:

**pkgdev commit**: commit to an ebuild repository

**pkgdev manifest**: update package manifests

**pkgdev mask**: mask packages

**pkgdev push**: scan commits for QA issues before pushing upstream

**pkgdev showkw**: show package keywords

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

    pip install .


.. _pkgcheck: https://github.com/pkgcore/pkgcheck
.. _pkgcore: https://github.com/pkgcore/pkgcore
.. _snakeoil: https://github.com/pkgcore/snakeoil
.. _dependencies: https://github.com/pkgcore/pkgdev/blob/main/requirements/install.txt

.. |pypi| image:: https://img.shields.io/pypi/v/pkgdev.svg
    :target: https://pypi.python.org/pypi/pkgdev
.. |test| image:: https://github.com/pkgcore/pkgdev/workflows/test/badge.svg
    :target: https://github.com/pkgcore/pkgdev/actions?query=workflow%3A%22test%22
.. |coverage| image:: https://codecov.io/gh/pkgcore/pkgdev/branch/main/graph/badge.svg
    :target: https://codecov.io/gh/pkgcore/pkgdev
