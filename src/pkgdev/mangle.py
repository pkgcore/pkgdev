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


def mangle(name):
    """Decorator to register file mangling methods."""

    class decorator:
        """Decorator with access to the class of a decorated function."""

        def __init__(self, func):
            self.func = func

        def __set_name__(self, owner, name):
            owner._mangle_funcs[name] = self.func
            setattr(owner, name, self.func)

    return decorator


class Mangler:
    """File-mangling iterator using path-based parallelism."""

    # mapping of mangling types to functions
    _mangle_funcs = {}

    def __init__(self, paths, skip_regex=None):
        self.jobs = os.cpu_count()
        if skip_regex is not None:
            paths = (x for x in paths if not skip_regex.match(x))
        self.paths = OrderedSet(paths)

        # setup for parallelizing the mangling procedure across files
        self._mp_ctx = multiprocessing.get_context('fork')
        self._mangled_paths_q = self._mp_ctx.SimpleQueue()
        self._current_year = str(datetime.today().year)

        # initialize settings used by iterator support
        self._runner = self._mp_ctx.Process(target=self._run)
        signal.signal(signal.SIGINT, self._kill_pipe)
        self._mangled_paths = iter(self._mangled_paths_q.get, None)

        # construct composed mangling function
        self.composed_func = functools.reduce(
            lambda f, g: lambda x: f(g(self, x)), self._mangle_funcs.values(), lambda x: x)

    @mangle('EOF')
    def _eof(self, data):
        """Drop EOF whitespace and forcibly add EOF newline."""
        return data.rstrip() + '\n'

    def _kill_pipe(self, *args, error=None):
        """Handle terminating the mangling process group."""
        if self._runner.is_alive():
            os.killpg(self._runner.pid, signal.SIGKILL)
        if error is not None:
            # propagate exception raised during parallelized mangling
            raise UserException(error)
        raise KeyboardInterrupt

    def __iter__(self):
        # start running the mangling processes
        self._runner.start()
        return self

    def __next__(self):
        try:
            path = next(self._mangled_paths)
        except StopIteration:
            self._runner.join()
            raise

        # Catch propagated, serialized exceptions, output their
        # traceback, and signal the scanning process to end.
        if isinstance(path, list):
            self._kill_pipe(error=path[0])

        return path

    def _mangle_file(self, path):
        """Run composed mangling function across a given file path."""
        try:
            with open(path, 'r+', encoding='utf-8') as f:
                if orig_data := f.read():
                    data = self.composed_func(orig_data)
                    if data != orig_data:
                        f.seek(0)
                        f.truncate()
                        f.write(data)
                        return path
        except (FileNotFoundError, UnicodeDecodeError):
            pass

    def _run_manglers(self, paths_q):
        """Consumer that runs mangling functions, queuing mangled paths for output."""
        try:
            for path in iter(paths_q.get, None):
                if mangled_path := self._mangle_file(path):
                    self._mangled_paths_q.put(mangled_path)
        except Exception:  # pragma: no cover
            # traceback can't be pickled so serialize it
            tb = traceback.format_exc()
            self._mangled_paths_q.put([tb])

    def _run(self):
        signal.signal(signal.SIGINT, signal.SIG_DFL)
        os.setpgrp()

        paths_q = self._mp_ctx.SimpleQueue()
        pool = self._mp_ctx.Pool(self.jobs, self._run_manglers, (paths_q,))
        pool.close()

        # queue paths for processing
        for path in self.paths:
            paths_q.put(path)
        # notify consumers that no more work exists
        for i in range(self.jobs):
            paths_q.put(None)

        pool.join()
        # notify iterator that no more results exist
        self._mangled_paths_q.put(None)


class GentooMangler(Mangler):
    """Gentoo repo specific file mangler."""

    _mangle_funcs = Mangler._mangle_funcs.copy()

    @mangle('copyright')
    def _copyright(self, data):
        """Fix copyright headers and dates."""
        lines = data.splitlines()
        if mo := copyright_regex.match(lines[0]):
            lines[0] = re.sub(mo.group('end'), self._current_year, lines[0])
            lines[0] = re.sub('Gentoo Foundation', 'Gentoo Authors', lines[0])
        return '\n'.join(lines) + '\n'
