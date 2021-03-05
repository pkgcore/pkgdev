"""Formatting and file mangling support."""

import functools
import multiprocessing
import os
import re
import signal
import traceback
from datetime import datetime

from snakeoil.cli.exceptions import UserException
from snakeoil.mappings import OrderedSet

copyright_regex = re.compile(
    r'^# Copyright (?P<begin>\d{4}-)?(?P<end>\d{4}) (?P<holder>.+)$')


class Mangler:
    """File-mangling iterator using path-based parallelism."""

    def __init__(self, repo, paths):
        self.jobs = os.cpu_count()
        # regex matching file paths to skip
        _skip_regex = re.compile(r'^[^/]+/[^/]+/files/.+$')
        self.paths = OrderedSet(x for x in paths if not _skip_regex.match(x))

        # setup for parallelizing the mangling procedure across files
        self._mp_ctx = multiprocessing.get_context('fork')
        self._altered_paths_q = self._mp_ctx.SimpleQueue()
        self._current_year = str(datetime.today().year)

        # initialize settings used by iterator support
        self._runner = None
        signal.signal(signal.SIGINT, self._kill_pipe)
        self._altered_paths = iter(self._altered_paths_q.get, None)

        # construct composed mangling function
        funcs = (getattr(self, x) for x in dir(self) if x.startswith('_mangle_'))
        # don't use gentoo repo specific mangling for non-gentoo repos
        if repo.repo_id != 'gentoo':
            funcs = (x for x in funcs if not x.__name__.endswith('_gentoo'))
        self.composed_func = functools.reduce(
            lambda f, g: lambda x: f(g(x)), funcs, lambda x: x)

    def _mangle_copyright_gentoo(self, data):
        """Fix copyright headers and dates."""
        lines = data.splitlines()
        if mo := copyright_regex.match(lines[0]):
            lines[0] = re.sub(mo.group('end'), self._current_year, lines[0])
            lines[0] = re.sub('Gentoo Foundation', 'Gentoo Authors', lines[0])
        return '\n'.join(lines) + '\n'

    def _mangle_eof(self, data):
        """Drop EOF whitespace and forcibly add EOF newline."""
        return data.rstrip() + '\n'

    def _kill_pipe(self, *args, error=None):
        """Handle terminating the mangling process group."""
        if self._runner is not None:
            os.killpg(self._runner.pid, signal.SIGKILL)
        if error is not None:
            # propagate exception raised during parallelized mangling
            raise UserException(error)
        raise KeyboardInterrupt

    def __iter__(self):
        # start running the mangling processes
        self._runner = self._mp_ctx.Process(target=self._run)
        self._runner.start()
        return self

    def __next__(self):
        try:
            path = next(self._altered_paths)
        except StopIteration:
            self._runner.join()
            raise

        # Catch propagated, serialized exceptions, output their
        # traceback, and signal the scanning process to end.
        if isinstance(path, list):
            self._kill_pipe(error=path[0])

        return path

    def _run_manglers(self, work_q):
        """Consumer that runs mangling functions, queuing altered paths for output."""
        try:
            for path in iter(work_q.get, None):
                try:
                    with open(path, 'r+') as f:
                        if orig_data := f.read():
                            data = self.composed_func(orig_data)
                            if data != orig_data:
                                f.seek(0)
                                f.truncate()
                                f.write(data)
                                self._altered_paths_q.put(path)
                except FileNotFoundError:
                    pass
        except Exception:  # pragma: no cover
            # traceback can't be pickled so serialize it
            tb = traceback.format_exc()
            self._altered_paths_q.put([tb])

    def _run(self):
        signal.signal(signal.SIGINT, signal.SIG_DFL)
        os.setpgrp()

        work_q = self._mp_ctx.SimpleQueue()
        pool = self._mp_ctx.Pool(self.jobs, self._run_manglers, (work_q,))
        pool.close()

        # queue paths for processing
        for path in self.paths:
            work_q.put(path)
        # notify consumers that no more work exists
        for i in range(self.jobs):
            work_q.put(None)

        pool.join()
        # notify iterator that no more results exist
        self._altered_paths_q.put(None)
