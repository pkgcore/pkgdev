"""Various command-line specific support."""

import argparse
import configparser
import logging
import os

from pkgcore.util import commandline
from snakeoil.cli import arghparse
from snakeoil.contexts import patch
from snakeoil.klass import jit_attr_none
from snakeoil.mappings import OrderedSet
from pkgcore.repository import errors as repo_errors
from pkgcore.util.commandline import _mk_domain

from . import const


class Tool(commandline.Tool):
    def main(self):
        # suppress all pkgcore log messages
        logging.getLogger("pkgcore").setLevel(100)
        return super().main()


class ConfigArg(argparse._StoreAction):
    """Store config path string or False when explicitly disabled."""

    def __call__(self, parser, namespace, values, option_string=None):
        if values.lower() in ("false", "no", "n"):
            values = False
        setattr(namespace, self.dest, values)


class ConfigParser(configparser.ConfigParser):
    """ConfigParser with case-sensitive keys (default forces lowercase)."""

    def optionxform(self, option):
        return option


class ConfigFileParser:
    """Argument parser that supports loading settings from specified config files."""

    default_configs = (const.SYSTEM_CONF_FILE, const.USER_CONF_FILE)

    def __init__(self, parser: arghparse.ArgumentParser, configs=(), **kwargs):
        super().__init__(**kwargs)
        self.parser = parser
        self.configs = OrderedSet(configs)

    @jit_attr_none
    def config(self):
        return self.parse_config()

    def parse_config(self, configs=()):
        """Parse given config files."""
        configs = configs if configs else self.configs
        config = ConfigParser(default_section=None)
        try:
            for f in configs:
                config.read(f)
        except configparser.ParsingError as e:
            self.parser.error(f"parsing config file failed: {e}")
        return config

    def parse_config_sections(self, namespace, sections):
        """Parse options from a given iterable of config section names."""
        assert self.parser.prog.startswith("pkgdev ")
        module = self.parser.prog.split(" ", 1)[1] + "."
        with patch("snakeoil.cli.arghparse.ArgumentParser.error", self._config_error):
            for section in (x for x in sections if x in self.config):
                config_args = (
                    (k.split(".", 1)[1], v)
                    for k, v in self.config.items(section)
                    if k.startswith(module)
                )
                config_args = (f"--{k}={v}" if v else f"--{k}" for k, v in config_args)
                namespace, args = self.parser.parse_known_optionals(config_args, namespace)
                if args:
                    self.parser.error(f"unknown arguments: {'  '.join(args)}")
        return namespace

    def parse_config_options(self, namespace, configs=()):
        """Parse options from config if they exist."""
        configs = list(filter(os.path.isfile, configs))
        if not configs:
            return namespace

        self.configs.update(configs)
        # reset jit attr to force reparse
        self._config = None

        # load default options
        namespace = self.parse_config_sections(namespace, ["DEFAULT"])

        return namespace

    def _config_error(self, message, status=2):
        """Stub to replace error method that notes config failure."""
        self.parser.exit(status, f"{self.parser.prog}: failed loading config: {message}\n")


class ArgumentParser(arghparse.ArgumentParser):
    """Parse all known arguments, from command line and config file."""

    def __init__(self, parents=(), **kwargs):
        self.config_argparser = arghparse.ArgumentParser(suppress=True)
        config_options = self.config_argparser.add_argument_group("config options")
        config_options.add_argument(
            "--config",
            action=ConfigArg,
            dest="config_file",
            help="use custom pkgdev settings file",
            docs="""
                Load custom pkgdev scan settings from a given file.

                Note that custom user settings override all other system and repo-level
                settings.

                It's also possible to disable all types of settings loading by
                specifying an argument of 'false' or 'no'.
            """,
        )
        _mk_domain(config_options)
        super().__init__(parents=[*parents, self.config_argparser], **kwargs)

    def parse_known_args(self, args=None, namespace=None):
        temp_namespace, _ = self.config_argparser.parse_known_args(args, namespace)
        # parser supporting config file options
        config_parser = ConfigFileParser(self)
        # always load settings from bundled config
        namespace = config_parser.parse_config_options(namespace, configs=[const.BUNDLED_CONF_FILE])

        # load default args from system/user configs if config-loading is allowed
        if temp_namespace.config_file is None:
            namespace = config_parser.parse_config_options(
                namespace, configs=ConfigFileParser.default_configs
            )
        elif temp_namespace.config_file is not False:
            namespace = config_parser.parse_config_options(
                namespace, configs=(namespace.config_file,)
            )

        try:
            repo = temp_namespace.domain.find_repo(
                os.getcwd(), config=temp_namespace.config, configure=False
            )
            if repo is not None:
                namespace = config_parser.parse_config_sections(namespace, repo.aliases)
        except (repo_errors.InitializationError, IOError) as exc:
            self.error(str(exc))

        if os.getenv("NOCOLOR"):
            namespace.color = False

        # parse command line args to override config defaults
        return super().parse_known_args(args, namespace)
