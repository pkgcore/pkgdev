"""Formatting and file mangling support."""

import re
import traceback
from datetime import UTC, datetime
from typing import ClassVar

from pkgcore.ebuild.misc import sort_keywords
from snakeoil.cli.exceptions import UserException
from snakeoil.mappings import OrderedSet

copyright_regex = re.compile(
    r"^# Copyright (?P<date>(?P<begin>\d{4}-)?(?P<end>\d{4})) (?P<holder>.+)$"
)

keywords_regex = re.compile(
    r'^(?P<pre>[^#]*\bKEYWORDS=(?P<quote>[\'"]?))(?P<keywords>.*)(?P<post>(?P=quote).*)$'
)


def mangle(name: str):
    """Decorator to register file mangling methods."""

    class decorator:
        """Decorator with access to the class of a decorated function."""

        def __init__(self, func):
            self.func = func

        def __set_name__(self, owner, attr):
            owner._mangle_funcs[name] = self.func
            setattr(owner, attr, self.func)

    return decorator


class Mangler:
    """File-mangling iterator, yielding the path of every change it mangles."""

    # mapping of mangling types to functions
    _mangle_funcs: ClassVar[dict[str, callable]] = {}

    def __init__(self, changes, skip_regex=None):
        if skip_regex is not None:
            changes = (c for c in changes if not skip_regex.match(c.full_path))
        self.changes = OrderedSet(changes)
        self._current_year = str(datetime.now(tz=UTC).year)

    @mangle("EOF")
    def _eof(self, change):
        """Drop EOF whitespace and forcibly add EOF newline."""
        return change.update(change.data.rstrip() + "\n")

    @mangle("eapi-blank-line")
    def _eapi_blank_line(self, change):
        """Add the blank line after the EAPI assignment."""
        lines = change.data.splitlines()
        for i, line in enumerate(lines):
            if line.startswith("EAPI="):
                if i + 1 < len(lines) and lines[i + 1] != "":
                    lines.insert(i + 1, "")
                break
        return change.update("\n".join(lines) + "\n")

    @mangle("keywords")
    def _keywords(self, change):
        """Fix keywords order."""
        lines = change.data.splitlines()
        for i, line in enumerate(lines):
            if mo := keywords_regex.match(line):
                new_kw = " ".join(sort_keywords(mo.group("keywords").split()))
                if not mo.group("quote"):
                    new_kw = f'"{new_kw}"'
                lines[i] = f"{mo.group('pre')}{new_kw}{mo.group('post')}"
                break
        return change.update("\n".join(lines) + "\n")

    def __iter__(self):
        try:
            for change in self.changes:
                if mangled_change := self._mangle(change):
                    yield mangled_change.path
        except Exception:
            # report the failure as a user error rather than a traceback
            raise UserException(traceback.format_exc()) from None

    def _mangle(self, change):
        """Run every registered mangling function across a given change."""
        if orig_data := change.read():
            # mangling functions run in reverse registration order
            for func in reversed(self._mangle_funcs.values()):
                change = func(self, change)
            if change.data != orig_data:
                change.sync()
                return change


class GentooMangler(Mangler):
    """Gentoo repo specific file mangler."""

    _mangle_funcs = Mangler._mangle_funcs.copy()

    @mangle("copyright")
    def _copyright(self, change):
        """Fix copyright headers and dates."""
        lines = change.data.splitlines()
        if mo := copyright_regex.match(lines[0]):
            groups = mo.groupdict()
            if groups["begin"] is None and groups["date"] != self._current_year:
                # use old copyright date as the start of date range
                date_range = f"{groups['date']}-{self._current_year}"
                lines[0] = re.sub(groups["date"], date_range, lines[0])
            else:
                lines[0] = re.sub(mo.group("end"), self._current_year, lines[0])
            lines[0] = re.sub("Gentoo Foundation", "Gentoo Authors", lines[0])
        return change.update("\n".join(lines) + "\n")
