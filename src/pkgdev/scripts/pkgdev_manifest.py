import os

from pkgcore.operations import observer as observer_mod
from pkgcore.restrictions import packages
from pkgcore.util.parserestrict import parse_match
from snakeoil.cli import arghparse

from .argparsers import cwd_repo_argparser

manifest = arghparse.ArgumentParser(
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
manifest.add_argument(
    '-f', '--force', help='forcibly remanifest specified packages',
    action='store_true',
    docs="""
        Force package manifest files to be rewritten. Note that this requires
        downloading all distfiles.
    """)
manifest.add_argument(
    '-m', '--mirrors', help='enable fetching from Gentoo mirrors',
    action='store_true',
    docs="""
        Enable checking Gentoo mirrors first for distfiles. This is disabled by
        default because manifest generation is often performed when adding new
        ebuilds with distfiles that aren't on Gentoo mirrors yet.
    """)


@manifest.bind_final_check
def _manifest_validate(parser, namespace):
    targets = namespace.target if namespace.target else [namespace.cwd]
    restrictions = []

    for target in targets:
        if os.path.exists(target):
            try:
                restrictions.append(namespace.repo.path_restrict(target))
            except ValueError as e:
                manifest.error(e)
        else:
            try:
                restrictions.append(parse_match(target))
            except ValueError:
                manifest.error(f'invalid atom: {target!r}')

    namespace.restriction = packages.OrRestriction(*restrictions)


@manifest.bind_main_func
def _manifest(options, out, err):
    failed = options.repo.operations.digests(
        domain=options.domain,
        restriction=options.restriction,
        observer=observer_mod.formatter_output(out),
        mirrors=options.mirrors,
        force=options.force)

    return int(any(failed))
