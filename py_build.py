from functools import partial
from pathlib import Path

from flit_core import buildapi


def write_verinfo(cleanup_files):
    from snakeoil.version import get_git_version

    cleanup_files.append(path := Path.cwd() / "src/pkgdev/_verinfo.py")
    path.parent.mkdir(parents=True, exist_ok=True)
    print(f"generating version info: {path}")
    path.write_text(f"version_info={get_git_version(Path.cwd())!r}")


def prepare_pkgcore(callback):
    cleanup_files = []
    try:
        write_verinfo(cleanup_files)

        return callback()
    finally:
        for path in cleanup_files:
            try:
                path.unlink()
            except OSError:
                pass


def build_wheel(wheel_directory, config_settings=None, metadata_directory=None):
    """Builds a wheel, places it in wheel_directory"""
    callback = partial(buildapi.build_wheel, wheel_directory, config_settings, metadata_directory)
    return prepare_pkgcore(callback)


def build_editable(wheel_directory, config_settings=None, metadata_directory=None):
    """Builds an "editable" wheel, places it in wheel_directory"""
    callback = partial(
        buildapi.build_editable, wheel_directory, config_settings, metadata_directory
    )
    return prepare_pkgcore(callback)


def build_sdist(sdist_directory, config_settings=None):
    """Builds an sdist, places it in sdist_directory"""
    callback = partial(buildapi.build_sdist, sdist_directory, config_settings)
    return prepare_pkgcore(callback)
