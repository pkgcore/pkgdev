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
    r'^# Copyright (?P<date>(?P<begin>\d{4}-)?(?P<end>\d{4})) (?P<holder>.+)$')

keywords_regex = re.compile(
    r'^(?P<pre>[^#]*\bKEYWORDS=(?P<quote>[\'"]?))'
    r'(?P<keywords>.*)'
    r'(?P<post>(?P=quote).*)$')


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

    def __init__(self, changes, skip_regex=None):
        self.jobs = os.cpu_count()
        if skip_regex is not None:
            changes = (c for c in changes if not skip_regex.match(c.full_path))
        self.changes = OrderedSet(changes)

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
    def _eof(self, change):
        """Drop EOF whitespace and forcibly add EOF newline."""
        return change.update(change.data.rstrip() + '\n')

    @mangle('keywords')
    def _keywords(self, change):
        """Fix keywords order."""

        def keywords_sort_key(kw):
            return tuple(reversed(kw.lstrip('-~').partition('-')))

        lines = change.data.splitlines()
        for i, line in enumerate(lines):
            if mo := keywords_regex.match(line):
                kw = sorted(mo.group('keywords').split(), key=keywords_sort_key)
                new_kw = ' '.join(kw)
                if not mo.group('quote'):
                    new_kw = f'"{new_kw}"'
                lines[i] = f'{mo.group("pre")}{new_kw}{mo.group("post")}'
                break
        return change.update('\n'.join(lines) + '\n')

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

    def _mangle(self, change):
        """Run composed mangling function across a given change."""
        if orig_data := change.read():
            change = self.composed_func(change)
            if change.data != orig_data:
                change.sync()
                return change

    def _run_manglers(self, paths_q):
        """Consumer that runs mangling functions, queuing mangled paths for output."""
        try:
            for change in iter(paths_q.get, None):
                if mangled_change := self._mangle(change):
                    self._mangled_paths_q.put(mangled_change.path)
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
        for change in self.changes:
            paths_q.put(change)
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
    def _copyright(self, change):
        """Fix copyright headers and dates."""
        lines = change.data.splitlines()
        if mo := copyright_regex.match(lines[0]):
            groups = mo.groupdict()
            if groups['begin'] is None and groups['date'] != self._current_year:
                # use old copyright date as the start of date range
                date_range = f"{groups['date']}-{self._current_year}"
                lines[0] = re.sub(groups['date'], date_range, lines[0])
            else:
                lines[0] = re.sub(mo.group('end'), self._current_year, lines[0])
            lines[0] = re.sub('Gentoo Foundation', 'Gentoo Authors', lines[0])
        return change.update('\n'.join(lines) + '\n')
