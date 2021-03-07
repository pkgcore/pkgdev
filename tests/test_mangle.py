import os
import multiprocessing
import re
import signal
from unittest.mock import patch

from pkgdev.mangle import Mangler
import pytest
from snakeoil.cli.exceptions import UserException


class TestMangler:

    def test_nonexistent_file(self, tmp_path):
        path = tmp_path / 'nonexistent'
        assert list(Mangler([str(path)])) == []

    def test_empty_file(self, tmp_path):
        path = tmp_path / 'empty'
        path.touch()
        assert list(Mangler([str(path)])) == []

    def test_skipped_file(self, tmp_path):
        paths = [(tmp_path / x) for x in ('file', 'file.patch')]

        for p in paths:
            p.write_text('# comment')
        # skip patch files
        skip_regex = re.compile(r'.+\.patch$')
        mangled_paths = set(Mangler(map(str, paths), skip_regex=skip_regex))
        assert mangled_paths == {str(tmp_path / 'file')}

        for p in paths:
            p.write_text('# comment')
        # don't skip any files
        mangled_paths = set(Mangler(map(str, paths)))
        assert mangled_paths == set(map(str, paths))

    def test_nonmangled_file(self, tmp_path):
        path = tmp_path / 'file'
        path.write_text('# comment\n')
        assert list(Mangler([str(path)])) == []

    def test_mangled_file(self, tmp_path):
        path = tmp_path / 'file'
        path.write_text('# comment')
        assert list(Mangler([str(path)])) == [str(path)]
        assert path.read_text() == '# comment\n'

    def test_iterator_exceptions(self, tmp_path):
        """Test parallelized iterator against unhandled exceptions."""
        path = tmp_path / 'file'
        path.write_text('# comment\n')

        def _mangle_func(self, data):
            raise Exception('func failed')

        with patch('pkgdev.mangle.Mangler._mangle_file', _mangle_func):
            with pytest.raises(UserException, match='Exception: func failed'):
                list(Mangler([str(path)]))

    def test_sigint_handling(self, tmp_path):
        """Verify SIGINT is properly handled by the parallelized pipeline."""
        path = tmp_path / 'file'
        path.write_text('# comment\n')

        def run(queue):
            """Mangler run in a separate process that gets interrupted."""
            import sys
            import time
            from functools import partial
            from unittest.mock import patch

            from pkgdev.mangle import Mangler

            def sleep():
                """Notify testing process then sleep."""
                queue.put('ready')
                time.sleep(100)

            with patch('pkgdev.mangle.Mangler.__iter__') as fake_iter:
                fake_iter.side_effect = partial(sleep)
                try:
                    iter(Mangler([str(path)]))
                except KeyboardInterrupt:
                    queue.put(None)
                    sys.exit(0)
                queue.put(None)
                sys.exit(1)

        mp_ctx = multiprocessing.get_context('fork')
        queue = mp_ctx.SimpleQueue()
        p = mp_ctx.Process(target=run, args=(queue,))
        p.start()
        # wait for pipeline object to be fully initialized then send SIGINT
        for _ in iter(queue.get, None):
            os.kill(p.pid, signal.SIGINT)
            p.join()
            assert p.exitcode == 0
