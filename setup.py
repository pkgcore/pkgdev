#!/usr/bin/env python3

from setuptools import setup
from snakeoil.dist import distutils_extensions as pkgdist

pkgdist_setup, pkgdist_cmds = pkgdist.setup()


setup(**dict(
    pkgdist_setup,
    license='BSD',
    author='Tim Harder',
    author_email='radhermit@gmail.com',
    description='collection of tools for Gentoo development',
    url='https://github.com/pkgcore/pkgdev',
    classifiers=[
        'License :: OSI Approved :: BSD License',
        'Programming Language :: Python :: 3.8',
        'Programming Language :: Python :: 3.9',
    ],
))
