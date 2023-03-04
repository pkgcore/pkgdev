import textwrap

import pytest
from pkgdev import cli
from snakeoil.cli import arghparse


class TestConfigFileParser:
    @pytest.fixture(autouse=True)
    def _create_argparser(self, tmp_path):
        self.config_file = str(tmp_path / "config")
        self.parser = arghparse.ArgumentParser(prog="pkgdev cli_test")
        self.namespace = arghparse.Namespace()
        self.config_parser = cli.ConfigFileParser(self.parser)

    def test_no_configs(self):
        config = self.config_parser.parse_config(())
        assert config.sections() == []
        namespace = self.config_parser.parse_config_options(self.namespace)
        assert vars(namespace) == {}

    def test_ignored_configs(self):
        # nonexistent config files are ignored
        config = self.config_parser.parse_config(("foo", "bar"))
        assert config.sections() == []

    def test_bad_config_format_no_section(self, capsys):
        with open(self.config_file, "w") as f:
            f.write("foobar\n")
        with pytest.raises(SystemExit) as excinfo:
            self.config_parser.parse_config((self.config_file,))
        out, err = capsys.readouterr()
        assert not out
        assert "parsing config file failed: File contains no section headers" in err
        assert self.config_file in err
        assert excinfo.value.code == 2

    def test_bad_config_format(self, capsys):
        with open(self.config_file, "w") as f:
            f.write(
                textwrap.dedent(
                    """
                        [DEFAULT]
                        foobar
                    """
                )
            )
        with pytest.raises(SystemExit) as excinfo:
            self.config_parser.parse_config((self.config_file,))
        out, err = capsys.readouterr()
        assert not out
        assert "parsing config file failed: Source contains parsing errors" in err
        assert excinfo.value.code == 2

    def test_nonexistent_config_options(self, capsys):
        """Nonexistent parser arguments don't cause errors."""
        with open(self.config_file, "w") as f:
            f.write(
                textwrap.dedent(
                    """
                        [DEFAULT]
                        cli_test.foo=bar
                    """
                )
            )
        with pytest.raises(SystemExit) as excinfo:
            self.config_parser.parse_config_options(None, configs=(self.config_file,))
        out, err = capsys.readouterr()
        assert not out
        assert "failed loading config: unknown arguments: --foo=bar" in err
        assert excinfo.value.code == 2

    def test_config_options_other_prog(self):
        self.parser.add_argument("--foo")
        with open(self.config_file, "w") as f:
            f.write(
                textwrap.dedent(
                    """
                        [DEFAULT]
                        other.foo=bar
                    """
                )
            )
        namespace = self.parser.parse_args(["--foo", "foo"])
        assert namespace.foo == "foo"
        # config args don't override not matching namespace attrs
        namespace = self.config_parser.parse_config_options(namespace, configs=[self.config_file])
        assert namespace.foo == "foo"

    def test_config_options(self):
        self.parser.add_argument("--foo")
        with open(self.config_file, "w") as f:
            f.write(
                textwrap.dedent(
                    """
                        [DEFAULT]
                        cli_test.foo=bar
                    """
                )
            )
        namespace = self.parser.parse_args(["--foo", "foo"])
        assert namespace.foo == "foo"
        # config args override matching namespace attrs
        namespace = self.config_parser.parse_config_options(namespace, configs=[self.config_file])
        assert namespace.foo == "bar"
