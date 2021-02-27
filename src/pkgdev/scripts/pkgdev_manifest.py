from snakeoil.cli import arghparse


manifest = arghparse.ArgumentParser(
    prog='pkgdev manifest', description='update package manifests')


@manifest.bind_main_func
def _manifest(options, out, err):
    return 0
