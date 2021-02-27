#!/usr/bin/env python3

import os

from setuptools import setup
from snakeoil.dist import distutils_extensions as pkgdist

pkgdist_setup, pkgdist_cmds = pkgdist.setup()


class test(pkgdist.pytest):
    """Wrapper to enforce testing against built version."""

    def run(self):
        # This is fairly hacky, but is done to ensure that the tests
        # are ran purely from what's in build, reflecting back to the source config data.
        key = 'PKGER_OVERRIDE_REPO_PATH'
        original = os.environ.get(key)
        try:
            os.environ[key] = os.path.dirname(os.path.realpath(__file__))
            super().run()
        finally:
            if original is not None:
                os.environ[key] = original
            else:
                os.environ.pop(key, None)


setup(**dict(
    pkgdist_setup,
    license='BSD',
    author='Tim Harder',
    author_email='radhermit@gmail.com',
    description='collection of tools for Gentoo development and maintenance',
    url='https://github.com/pkgcore/pkgdev',
    cmdclass=dict(
        pkgdist_cmds,
        test=test,
    ),
    classifiers=[
        'License :: OSI Approved :: BSD License',
        'Programming Language :: Python :: 3.8',
        'Programming Language :: Python :: 3.9',
    ],
))
