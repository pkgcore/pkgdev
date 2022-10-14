import os
import re
import subprocess

from pkgcore.operations import observer as observer_mod
from pkgcore.restrictions import packages, values
from pkgcore.util.parserestrict import parse_match
from snakeoil.cli import arghparse

from .. import cli, git
from .argparsers import cwd_repo_argparser

manifest = cli.ArgumentParser(
    prog='pkgdev manifest', description='update package manifests',
    parents=(cwd_repo_argparser,))
manifest.add_argument(
    'target', nargs='*',
    help='packages to target',
    docs="""
        Packages matching any of these restrictions will have their manifest
        entries updated. If no target is specified a path restriction is
        created based on the current working directory. In other words, if
        ``pkgdev manifest`` is run within an ebuild's directory, all the
        ebuilds within that directory will be manifested.
    """)
manifest_opts = manifest.add_argument_group('manifest options')
manifest_opts.add_argument(
    '-d', '--distdir', type=arghparse.create_dir, help='target download directory',
    docs="""
        Use a specified target directory for downloads instead of the
        configured DISTDIR.
    """)
manifest_opts.add_argument(
    '-f', '--force', help='forcibly remanifest packages',
    action='store_true',
    docs="""
        Force package manifest files to be rewritten. Note that this requires
        downloading all distfiles.
    """)
manifest_opts.add_argument(
    '-m', '--mirrors', help='enable fetching from Gentoo mirrors',
    action='store_true',
    docs="""
        Enable checking Gentoo mirrors first for distfiles. This is disabled by
        default because manifest generation is often performed when adding new
        ebuilds with distfiles that aren't on Gentoo mirrors yet.
    """)
manifest_opts.add_argument(
    '--if-modified', dest='if_modified', help='Only check packages that have uncommitted modifications',
    action='store_true',
    docs="""
        In addition to matching the specified restriction, restrict to targets
        which are marked as modified by git, including untracked files.
    """)
manifest_opts.add_argument(
    '--ignore-fetch-restricted', dest='ignore_fetch_restricted', help='Ignore fetch restricted ebuilds',
    action='store_true',
    docs="""
        Ignore attempting to update manifest entries for ebuilds which are
        fetch restricted.
    """)


def _restrict_targets(repo, targets):
    restrictions = []
    for target in targets:
        if os.path.exists(target):
            try:
                if target in repo:
                    target = os.path.relpath(target, repo.location)
                restrictions.append(repo.path_restrict(target))
            except ValueError as exc:
                manifest.error(exc)
        else:
            try:
                restrictions.append(parse_match(target))
            except ValueError:
                manifest.error(f'invalid atom: {target!r}')
    return packages.OrRestriction(*restrictions)


def _restrict_modified_files(repo):
    ebuild_re = re.compile(r'^[ MTARC?]{2} (?P<path>[^/]+/[^/]+/[^/]+\.ebuild)$')
    p = git.run('status', '--porcelain=v1', '-z', "*.ebuild",
                cwd=repo.location, stdout=subprocess.PIPE)

    restrictions = []
    for line in p.stdout.strip('\x00').split('\x00'):
        if mo := ebuild_re.match(line):
            restrictions.append(repo.path_restrict(mo.group('path')))
    return packages.OrRestriction(*restrictions)


@manifest.bind_final_check
def _manifest_validate(parser, namespace):
    targets = namespace.target if namespace.target else [namespace.cwd]

    restrictions = [_restrict_targets(namespace.repo, targets)]
    if namespace.if_modified:
        restrictions.append(_restrict_modified_files(namespace.repo))
    if namespace.ignore_fetch_restricted:
        restrictions.append(packages.PackageRestriction('restrict', values.ContainmentMatch('fetch', negate=True)))
    namespace.restriction = packages.AndRestriction(*restrictions)


@manifest.bind_main_func
def _manifest(options, out, err):
    failed = options.repo.operations.manifest(
        domain=options.domain,
        restriction=options.restriction,
        observer=observer_mod.formatter_output(out),
        mirrors=options.mirrors,
        force=options.force,
        distdir=options.distdir)

    return int(any(failed))
