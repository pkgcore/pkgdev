"""collection of tools for Gentoo development

pkgdev provides a collection of tools for Gentoo development.
"""

from pkgcore.util import commandline

argparser = commandline.ArgumentParser(
    description=__doc__, help=False, subcmds=True, script=(__file__, __name__))
