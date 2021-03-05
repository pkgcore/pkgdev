from unittest.mock import patch

from pkgdev.mangle import Mangler
import pytest
from snakeoil.cli.exceptions import UserException
from snakeoil.fileutils import touch
from snakeoil.osutils import pjoin


class TestMangler:

    def test_nonexistent_file(self, repo):
        path = pjoin(repo.location, 'nonexistent')
        assert list(Mangler(repo, [path])) == []

    def test_empty_file(self, repo):
        path = pjoin(repo.location, 'empty')
        touch(path)
        assert list(Mangler(repo, [path])) == []

    def test_nonmangled_file(self, repo):
        path = pjoin(repo.location, 'file')
        with open(path, 'w') as f:
            f.write('# comment\n')
        assert list(Mangler(repo, [path])) == []

    def test_mangled_file(self, repo):
        path = pjoin(repo.location, 'file')
        with open(path, 'w') as f:
            f.write('# comment')
        assert list(Mangler(repo, [path])) == [path]
        with open(path, 'r') as f:
            assert f.read() == '# comment\n'

    def test_iterator_exceptions(self, repo):
        """Test parallelized iterator against unhandled exceptions."""
        path = pjoin(repo.location, 'file')
        with open(path, 'w') as f:
            f.write('# comment\n')

        def _mangle_func(self, data):
            raise Exception('func failed')

        with patch('pkgdev.mangle.Mangler._mangle_eof', _mangle_func):
            with pytest.raises(UserException, match='Exception: func failed'):
                list(Mangler(repo, [path]))
