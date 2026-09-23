"""Argv helpers: `--` ends options, quoting survives a paste."""
from __future__ import annotations

import io
import os

from operator_cli.argv import isatty, quote_argv
from argtail import at_dashdash


def _split_printed(command: str) -> list[str]:
    if os.name != "nt":
        import shlex
        return shlex.split(command)
    out, i, n = [], 0, len(command)
    while i < n:
        if command[i].isspace():
            i += 1
            continue
        if command[i] == "'":
            i += 1
            buf = []
            while i < n:
                if command[i] == "'" and i + 1 < n and command[i + 1] == "'":
                    buf.append("'")
                    i += 2
                    continue
                if command[i] == "'":
                    i += 1
                    break
                buf.append(command[i])
                i += 1
            out.append("".join(buf))
            continue
        j = i
        while j < n and not command[j].isspace():
            j += 1
        out.append(command[i:j])
        i = j
    return out


def test_at_dashdash_keeps_the_tail_literal():
    assert at_dashdash(
        ["remember", "--instance", "alpha", "--", "--instance=beta", "text"]
    ) == (
        ["remember", "--instance", "alpha"],
        ["--", "--instance=beta", "text"],
    )


def test_at_dashdash_without_terminator_is_unchanged():
    argv = ["remember", "--instance", "alpha"]
    assert at_dashdash(argv) == (argv, [])


def test_quote_round_trips_a_quote_and_a_dollar():
    argv = ["remember", "--kind", "gotcha", "can't look at $HOME"]
    quoted = quote_argv(argv)
    assert _split_printed(quoted) == argv
    assert "$HOME" in quoted
    if os.name == "nt":
        assert "can''t look at $HOME" in quoted


def test_isatty_treats_none_as_not_a_tty():
    assert isatty(None) is False


def test_isatty_treats_a_closed_stream_as_not_a_tty():
    closed = io.StringIO()
    closed.close()
    assert isatty(closed) is False
