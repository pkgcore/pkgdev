"""package testing tool"""

import os
import random
import stat
from collections import defaultdict
from importlib.resources import read_text
from itertools import islice
from pathlib import Path

from pkgcore.restrictions import boolean, packages, values
from pkgcore.restrictions.required_use import find_constraint_satisfaction
from pkgcore.util import commandline
from pkgcore.util import packages as pkgutils
from snakeoil.cli import arghparse

from ..cli import ArgumentParser
from .argparsers import BugzillaApiKey

tatt = ArgumentParser(prog="pkgdev tatt", description=__doc__, verbose=False, quiet=False)
BugzillaApiKey.mangle_argparser(tatt)
tatt.add_argument(
    "-j",
    "--job-name",
    metavar="NAME",
    default="{PN}-{BUGNO}",
    help="Name template for created job script",
    docs="""
        The job name to use for the job script and report. The name can use
        the variables ``{PN}`` (package name) and ``{BUGNO}`` (bug number)
        to created variable names.
    """,
)
tatt.add_argument(
    "-b",
    "--bug",
    type=arghparse.positive_int,
    metavar="BUG",
    help="Single bug to take package list from",
)

use_opts = tatt.add_argument_group("Use flags options")
use_opts.add_argument(
    "-t",
    "--test",
    action="store_true",
    help="Run test phase for the packages",
    docs="""
        Include a test run for packages which define ``src_test`` phase
        (in the ebuild or inherited from eclass).
    """,
)
use_opts.add_argument(
    "-u",
    "--use-combos",
    default=0,
    type=arghparse.positive_int,
    metavar="NUMBER",
    help="Maximal number USE combinations to be tested",
)
use_opts.add_argument(
    "--ignore-prefixes",
    default=[],
    action=arghparse.CommaSeparatedValuesAppend,
    help="USE flags prefixes that won't be randomized",
    docs="""
        Comma separated USE flags prefixes that won't be randomized. This is
        useful for USE flags such as ``python_targets_``. Note that this
        doesn't affect preference, but because of specific REQUIRED_USE will
        still be changed from defaults.
    """,
)
random_use_opts = use_opts.add_mutually_exclusive_group()
random_use_opts.add_argument(
    "--use-default",
    dest="random_use",
    const="d",
    action="store_const",
    help="Prefer to use default use flags configuration",
)
random_use_opts.add_argument(
    "--use-random",
    dest="random_use",
    const="r",
    action="store_const",
    help="Turn on random use flags, with default USE_EXPAND",
)
random_use_opts.add_argument(
    "--use-expand-random",
    dest="random_use",
    const="R",
    action="store_const",
    help="Turn on random use flags, including USE_EXPAND",
)
random_use_opts.set_defaults(random_use="r")

packages_opts = tatt.add_argument_group("manual packages options")
packages_opts.add_argument(
    "-p",
    "--packages",
    metavar="TARGET",
    nargs="+",
    help="extended atom matching of packages",
)
bug_state = packages_opts.add_mutually_exclusive_group()
bug_state.add_argument(
    "-s",
    "--stablereq",
    dest="keywording",
    default=None,
    action="store_false",
    help="Test packages for stable keywording requests",
)
bug_state.add_argument(
    "-k",
    "--keywording",
    dest="keywording",
    default=None,
    action="store_true",
    help="Test packages for keywording requests",
)

template_opts = tatt.add_argument_group("template options")
template_opts.add_argument(
    "--template-file",
    type=arghparse.existent_path,
    help="Template file to use for the job script",
    docs="""
        Template file to use for the job script. The template file is a
        Jinja template file, which can use the following variables:

        .. glossary::

            ``jobs``
                A list of jobs to be run. Each job is a tuple consisting of
                USE flags values, is a testing job, and the atom to build.

            ``report_file``
                The path to the report file.

            ``emerge_opts``
                Options to be passed to emerge invocations. Taken from
                ``--emerge-opts``.

            ``extra_env_files``
                A list of extra /etc/portage/env/ file names, to be added to
                ``package.env`` entry when testing the package. Taken from
                ``--extra-env-file``.

            ``log_dir``
                irectory to save build logs for failing tasks. Taken from
                ``--logs-dir``.

            ``cleanup_files``
                A list of files to be removed after the job script is done.
    """,
)
template_opts.add_argument(
    "--logs-dir",
    default="~/logs",
    help="Directory to save build logs for failing tasks",
)
template_opts.add_argument(
    "--emerge-opts",
    default="",
    help="Options to be passed to emerge invocations",
    docs="""
        Space separated single argument, consisting og options to be passed
        to ``emerge`` invocations.
    """,
)
template_opts.add_argument(
    "--extra-env-file",
    default=[],
    metavar="ENV_FILE",
    action=arghparse.CommaSeparatedValuesAppend,
    help="Extra /etc/portage/env/ file names, to be used while testing packages. Can be passed multiple times.",
    docs="""
        Comma separated filenames under /etc/portage/env/, which will all be
        included in the package.env entry when testing the package.
    """,
)

portage_config = Path("/etc/portage")
portage_accept_keywords = portage_config / "package.accept_keywords"
portage_package_use = portage_config / "package.use"
portage_package_env = portage_config / "package.env"
portage_env = portage_config / "env"


@tatt.bind_final_check
def _tatt_validate(parser, namespace):
    for filename in namespace.extra_env_file:
        if not (env_file := portage_env / filename).exists():
            parser.error(f"extra env file '{env_file}' doesn't exist")


@tatt.bind_final_check
def _validate_args(parser, namespace):
    if namespace.bug is not None:
        if namespace.keywording is not None:
            parser.error("cannot use --bug with --keywording or --stablereq")
        if namespace.packages:
            parser.error("cannot use --bug with --packages")
    elif not namespace.packages:
        parser.error("no action requested, use --bug or --packages")

    if not namespace.test and not namespace.use_combos:
        parser.error("no action requested, use --test or --use-combos")

    if namespace.packages:
        arch = namespace.domain.arch
        if namespace.keywording:
            keywords_restrict = packages.PackageRestriction(
                "keywords",
                values.ContainmentMatch((f"~{arch}", f"-{arch}", arch), negate=True),
            )
        else:
            keywords_restrict = packages.PackageRestriction(
                "keywords", values.ContainmentMatch((f"~{arch}", arch))
            )
        namespace.restrict = boolean.AndRestriction(
            boolean.OrRestriction(*commandline.convert_to_restrict(namespace.packages)),
            packages.PackageRestriction("properties", values.ContainmentMatch("live", negate=True)),
            keywords_restrict,
        )


def _get_bugzilla_packages(namespace):
    from nattka.bugzilla import BugCategory, NattkaBugzilla
    from nattka.package import match_package_list

    nattka_bugzilla = NattkaBugzilla(api_key=namespace.api_key)
    bug = next(iter(nattka_bugzilla.find_bugs(bugs=[namespace.bug]).values()))
    namespace.keywording = bug.category == BugCategory.KEYWORDREQ
    repo = namespace.domain.repos["gentoo"].raw_repo
    src_repo = namespace.domain.source_repos_raw
    for pkg, _ in match_package_list(repo, bug, only_new=True, filter_arch=[namespace.domain.arch]):
        yield src_repo.match(pkg.versioned_atom)[0]


def _get_cmd_packages(namespace):
    repos = namespace.domain.source_repos_raw
    for pkgs in pkgutils.groupby_pkg(repos.itermatch(namespace.restrict, sorter=sorted)):
        pkg = max(pkgs)
        yield pkg.repo.match(pkg.versioned_atom)[0]


def _groupby_use_expand(
    assignment: dict[str, bool],
    use_expand_prefixes: tuple[str, ...],
    domain_enabled: frozenset[str],
    iuse: frozenset[str],
):
    use_expand_dict: dict[str, set[str]] = defaultdict(set)
    use_flags: set[str] = set()
    for var, state in assignment.items():
        if var not in iuse:
            continue
        if state == (var in domain_enabled):
            continue
        for use_expand in use_expand_prefixes:
            if var.startswith(use_expand):
                if state:
                    use_expand_dict[use_expand[:-1]].add(var.removeprefix(use_expand))
                break
        else:
            use_flags.add(("" if state else "-") + var)
    return use_flags, use_expand_dict


def _build_job(namespace, pkg, is_test: bool):
    use_expand_prefixes = tuple(s.lower() + "_" for s in namespace.domain.profile.use_expand)
    default_on_iuse = tuple(use[1:] for use in pkg.iuse if use.startswith("+"))
    immutable, enabled, _disabled = namespace.domain.get_package_use_unconfigured(pkg)

    iuse = frozenset(pkg.iuse_stripped)
    force_true = immutable.union(("test",) if is_test else ())
    force_false = ("test",) if not is_test else ()

    if namespace.random_use == "d":
        prefer_true = enabled.union(default_on_iuse)
    elif namespace.random_use in "rR":
        ignore_prefixes = set(namespace.ignore_prefixes)
        if namespace.random_use == "r":
            ignore_prefixes.update(use_expand_prefixes)
        ignore_prefixes = tuple(ignore_prefixes)

        prefer_true = [
            use
            for use in iuse.difference(force_true, force_false)
            if not use.startswith(ignore_prefixes)
        ]
        if prefer_true:
            random.shuffle(prefer_true)
            prefer_true = prefer_true[: random.randint(0, len(prefer_true) - 1)]
        prefer_true.extend(
            use for use in enabled.union(default_on_iuse) if use.startswith(ignore_prefixes)
        )

    solutions = find_constraint_satisfaction(
        pkg.required_use,
        iuse.union(immutable),
        force_true,
        force_false,
        frozenset(prefer_true),
    )
    for solution in solutions:
        use_flags, use_expand = _groupby_use_expand(solution, use_expand_prefixes, enabled, iuse)
        yield " ".join(use_flags) + " " + " ".join(
            f'{var.upper()}: {" ".join(vals)}' for var, vals in use_expand.items()
        )


def _build_jobs(namespace, pkgs):
    for pkg in pkgs:
        for flags in islice(_build_job(namespace, pkg, False), namespace.use_combos):
            yield pkg.versioned_atom, False, flags

        if namespace.test and "test" in pkg.defined_phases:
            yield pkg.versioned_atom, True, next(iter(_build_job(namespace, pkg, True)))


def _create_config_dir(directory: Path):
    if not directory.exists():
        directory.mkdir(parents=True)
    elif not directory.is_dir():
        raise NotADirectoryError(f"{directory} is not a directory")


def _create_config_files(pkgs, job_name, is_keywording):
    _create_config_dir(portage_accept_keywords)
    with (res := portage_accept_keywords / f"pkgdev_tatt_{job_name}.keywords").open("w") as f:
        f.write(f"# Job created by pkgdev tatt for {job_name!r}\n")
        for pkg in pkgs:
            f.write(f'{pkg.versioned_atom} {"**" if is_keywording else ""}\n')
    yield str(res)

    _create_config_dir(portage_env)
    with (res := portage_env / f"pkgdev_tatt_{job_name}_no_test").open("w") as f:
        f.write(f"# Job created by pkgdev tatt for {job_name!r}\n")
        f.write('FEATURES="qa-unresolved-soname-deps multilib-strict"\n')
    yield str(res)
    with (res := portage_env / f"pkgdev_tatt_{job_name}_test").open("w") as f:
        f.write(f"# Job created by pkgdev tatt for {job_name!r}\n")
        f.write('FEATURES="qa-unresolved-soname-deps multilib-strict test"\n')
    yield str(res)

    _create_config_dir(portage_package_use)
    (res := portage_package_use / f"pkgdev_tatt_{job_name}").mkdir(exist_ok=True)
    yield str(res)
    _create_config_dir(portage_package_env)
    (res := portage_package_env / f"pkgdev_tatt_{job_name}").mkdir(exist_ok=True)
    yield str(res)


@tatt.bind_main_func
def main(options, out, err):
    if options.bug is not None:
        pkgs = tuple(_get_bugzilla_packages(options))
    else:
        pkgs = tuple(_get_cmd_packages(options))

    if not pkgs:
        return err.error("package query resulted in empty package list")

    job_name = options.job_name.format(PN=pkgs[0].package, BUGNO=options.bug or "")
    cleanup_files = []

    try:
        for config_file in _create_config_files(pkgs, job_name, options.keywording):
            out.write("created config ", out.fg("green"), config_file, out.reset)
            cleanup_files.append(config_file)
    except Exception as exc:
        err.error(f"failed to create config files: {exc}")

    if options.template_file:
        with open(options.template_file) as output:
            template = output.read()
    else:
        template = read_text("pkgdev.tatt", "template.sh.jinja")

    from jinja2 import Template

    script = Template(template, trim_blocks=True, lstrip_blocks=True).render(
        jobs=list(_build_jobs(options, pkgs)),
        report_file=job_name + ".report",
        job_name=job_name,
        log_dir=options.logs_dir,
        emerge_opts=options.emerge_opts,
        extra_env_files=options.extra_env_file,
        cleanup_files=cleanup_files,
    )
    with open(script_name := job_name + ".sh", "w") as output:
        output.write(script)
    os.chmod(script_name, os.stat(script_name).st_mode | stat.S_IEXEC)
    out.write("created script ", out.fg("green"), script_name, out.reset)
