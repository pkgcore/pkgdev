import os
import multiprocessing
import signal
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

    def test_sigint_handling(self, repo):
        """Verify SIGINT is properly handled by the parallelized pipeline."""
        path = pjoin(repo.location, 'file')
        with open(path, 'w') as f:
            f.write('# comment\n')

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
                    iter(Mangler(repo, [path]))
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
