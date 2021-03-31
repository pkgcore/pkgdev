import pytest


class TestPkgdevShowkwParseArgs:

    def test_missing_target(self, capsys, tool):
        with pytest.raises(SystemExit):
            tool.parse_args(['showkw'])
        captured = capsys.readouterr()
        assert captured.err.strip() == (
            'pkgdev showkw: error: missing target argument and not in a supported repo')

    def test_unknown_arches(self, capsys, tool, make_repo):
        repo = make_repo(arches=['amd64'])
        with pytest.raises(SystemExit):
            tool.parse_args(['showkw', '-a', 'unknown', '-r', repo.location])
        captured = capsys.readouterr()
        assert captured.err.strip() == (
            "pkgdev showkw: error: unknown arch: 'unknown' (choices: amd64)")
