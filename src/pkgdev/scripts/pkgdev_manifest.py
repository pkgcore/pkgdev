import os

from pkgcore.operations import observer as observer_mod
from pkgcore.util import commandline
from pkgcore.util.parserestrict import parse_match
from pkgcore.restrictions import packages
from snakeoil.cli import arghparse


manifest = arghparse.ArgumentParser(
    prog='pkgdev manifest', description='update package manifests')
manifest.add_argument(
    'target', nargs='*',
    help='packages to target',
    docs="""
        Packages matching any of these restrictions will have their manifest
        entries updated; however, if no target is specified one of the
        following two cases occurs:

        - If a repo is specified, the entire repo is manifested.
        - If a repo isn't specified, a path restriction is created based on the
          current working directory. In other words, if ``pkgdev manifest`` is run
          within an ebuild's directory, all the ebuilds within that directory
          will be manifested. If the current working directory isn't
          within any configured repo, all repos are manifested.
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
manifest.add_argument(
    '-r', '--repo', help='target repository',
    action=commandline.StoreRepoObject, repo_type='ebuild-raw', allow_external_repos=True,
    docs="""
        Target repository to search for matches. If no repo is specified all
        ebuild repos are used.
    """)


@manifest.bind_final_check
def _manifest_validate(parser, namespace):
    repo = namespace.repo
    targets = namespace.target
    restrictions = []
    if repo is not None:
        if not targets:
            restrictions.append(repo.path_restrict(repo.location))
    else:
        # if we're currently in a known ebuild repo use it, otherwise use all ebuild repos
        cwd = os.getcwd()
        repo = namespace.domain.ebuild_repos_raw.repo_match(cwd)
        if repo is None:
            repo = namespace.domain.all_ebuild_repos_raw

        if not targets:
            try:
                restrictions.append(repo.path_restrict(cwd))
            except ValueError:
                # we're not in a configured repo so manifest everything
                restrictions.extend(repo.path_restrict(x.location) for x in repo.trees)

    if not repo.operations.supports('digests'):
        manifest.error('no repository support for manifests')

    for target in targets:
        if os.path.exists(target):
            try:
                restrictions.append(repo.path_restrict(target))
            except ValueError as e:
                manifest.error(e)
        else:
            try:
                restrictions.append(parse_match(target))
            except ValueError:
                manifest.error(f'invalid atom: {target!r}')

    restriction = packages.OrRestriction(*restrictions)
    namespace.restriction = restriction
    namespace.repo = repo


@manifest.bind_main_func
def _manifest(options, out, err):
    repo = options.repo

    failed = repo.operations.digests(
        domain=options.domain,
        restriction=options.restriction,
        observer=observer_mod.formatter_output(out),
        mirrors=options.mirrors,
        force=options.force)

    return int(any(failed))
