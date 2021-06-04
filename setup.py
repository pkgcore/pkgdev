#!/usr/bin/env python3

from itertools import chain

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
    data_files=list(chain(
        pkgdist.data_mapping('share/bash-completion/completions', 'completion/bash'),
        pkgdist.data_mapping('share/zsh/site-functions', 'completion/zsh'),
    )),
    classifiers=[
        'License :: OSI Approved :: BSD License',
        'Programming Language :: Python :: 3.8',
        'Programming Language :: Python :: 3.9',
        'Programming Language :: Python :: 3.10',
    ],
))
