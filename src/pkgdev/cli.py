"""Various command-line specific support."""

import logging

from pkgcore.util import commandline


class Tool(commandline.Tool):

    def main(self):
        # suppress all pkgcore log messages
        logging.getLogger('pkgcore').setLevel(100)
        return super().main()
