"""Argv helpers: `--` ends options, and a missing stream is not a TTY."""
from __future__ import annotations

import io

from operator_cli.argv import isatty
from argtail import at_dashdash


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


def test_isatty_treats_none_as_not_a_tty():
    assert isatty(None) is False


def test_isatty_treats_a_closed_stream_as_not_a_tty():
    closed = io.StringIO()
    closed.close()
    assert isatty(closed) is False
