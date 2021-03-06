import os
import multiprocessing
import re
import signal
from unittest.mock import patch

from pkgdev.mangle import Mangler
import pytest
from snakeoil.cli.exceptions import UserException
from snakeoil.fileutils import touch
from snakeoil.osutils import pjoin


class TestMangler:

    def test_nonexistent_file(self, namespace, repo):
        options = namespace
        options.repo = repo
        path = pjoin(repo.location, 'nonexistent')
        assert list(Mangler(options, [path])) == []

    def test_empty_file(self, namespace, repo):
        options = namespace
        options.repo = repo
        path = pjoin(repo.location, 'empty')
        touch(path)
        assert list(Mangler(options, [path])) == []

    def test_skipped_file(self, namespace, repo):
        options = namespace
        options.repo = repo
        paths = [pjoin(repo.location, x) for x in ('file', 'file.patch')]
        skip_regex = re.compile(r'.+\.patch$')
        for p in paths:
            with open(p, 'w') as f:
                f.write('# comment')
        mangled_paths = list(Mangler(options, paths, skip_regex=skip_regex))
        assert mangled_paths == [pjoin(repo.location, 'file')]

    def test_nonmangled_file(self, namespace, repo):
        options = namespace
        options.repo = repo
        path = pjoin(repo.location, 'file')
        with open(path, 'w') as f:
            f.write('# comment\n')
        assert list(Mangler(options, [path])) == []

    def test_mangled_file(self, namespace, repo):
        options = namespace
        options.repo = repo
        path = pjoin(repo.location, 'file')
        with open(path, 'w') as f:
            f.write('# comment')
        assert list(Mangler(options, [path])) == [path]
        with open(path, 'r') as f:
            assert f.read() == '# comment\n'

    def test_iterator_exceptions(self, namespace, repo):
        """Test parallelized iterator against unhandled exceptions."""
        options = namespace
        options.repo = repo
        path = pjoin(repo.location, 'file')
        with open(path, 'w') as f:
            f.write('# comment\n')

        def _mangle_func(self, data):
            raise Exception('func failed')

        with patch('pkgdev.mangle.Mangler._mangle_file', _mangle_func):
            with pytest.raises(UserException, match='Exception: func failed'):
                list(Mangler(options, [path]))

    def test_sigint_handling(self, namespace, repo):
        """Verify SIGINT is properly handled by the parallelized pipeline."""
        options = namespace
        options.repo = repo
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
                    iter(Mangler(options, [path]))
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
