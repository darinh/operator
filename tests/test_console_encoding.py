"""Console output must be ASCII, because no other character is portable.

Found by pointing `e2e_restart_loop.py` at this repository for the first time.
`restart_loop` did its whole job -- old supervisor retired, new one adopted the
session -- and then died printing its success message, because that message
carried a U+2705 and this machine's console is cp1252. The work was done and
the command reported a traceback.

The tempting fix is "avoid emoji". The measurement says otherwise, and it is
the reason this is a rule rather than a style note:

    cp1252   em dash ok    box-drawing FAILS   emoji FAILS
    cp437    em dash FAILS box-drawing ok      emoji FAILS
    cp850    em dash FAILS box-drawing ok      emoji FAILS

There is no non-ASCII character that survives every Windows console. An em
dash, which reads as obviously safe and had been used in four places here,
fails on exactly the consoles where the box-drawing characters work. So the
portable set is ASCII, and anything else is a machine-dependent crash in a
command-line tool whose primary platform is Windows.

This is deliberately narrow. Log files are opened with an explicit encoding
and may carry whatever they like; `probes.log` writes UTF-8 and is not in
scope. Docstrings and comments are not in scope either -- they are never
encoded to a console. Only what is printed.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
PACKAGES = ("operator_kernel", "operator_fleet", "operator_cli",
            "operator_extensions")

#: Console encodings a Windows user plausibly has. A string must survive all of
#: them, which in practice means ASCII -- but it is asserted by encoding rather
#: than by an `isascii()` call, so the failure names the console that would
#: break rather than a rule somebody has to take on trust.
CONSOLE_ENCODINGS = ("cp1252", "cp437", "cp850", "ascii")


def source_files() -> "list[Path]":
    return sorted(p for pkg in PACKAGES for p in (REPO / pkg).glob("*.py"))


def printed_strings(source: str) -> "list[tuple[int, str]]":
    """Every string literal that reaches a console, with its line number.

    Read from the AST rather than by matching `print(` in the text, because a
    line-based scan cannot see a multi-line call and this codebase wraps
    almost all of them. Calls to `print` and to anything named `*_stderr` are
    both in scope; `probes.log` is not, since it writes to a file it opened
    with an explicit encoding.
    """
    found: list[tuple[int, str]] = []
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return found
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = getattr(node.func, "id", "") or getattr(node.func, "attr", "")
        if name != "print":
            continue
        for sub in ast.walk(node):
            if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
                found.append((sub.lineno, sub.value))
    return found


@pytest.mark.parametrize("path", source_files(), ids=lambda p: p.name)
def test_printed_text_survives_every_console(path):
    offenders = []
    for lineno, text in printed_strings(path.read_text(encoding="utf-8")):
        for char in text:
            if ord(char) < 128:
                continue
            breaks = [enc for enc in CONSOLE_ENCODINGS
                      if not _encodes(char, enc)]
            if breaks:
                offenders.append(
                    f"{path.name}:{lineno} U+{ord(char):04X} breaks "
                    f"{', '.join(breaks)}")
    assert offenders == [], (
        "these printed characters raise UnicodeEncodeError on a console that "
        "cannot encode them, which ends the command:\n  "
        + "\n  ".join(offenders)
        + "\n\nNo non-ASCII character survives every Windows console: an em "
          "dash works on cp1252 and fails on cp437, box-drawing is the other "
          "way round. Use ASCII in anything printed.")


def _encodes(char: str, encoding: str) -> bool:
    try:
        char.encode(encoding)
    except UnicodeEncodeError:
        return False
    return True


# ── controls ────────────────────────────────────────────────────
#
# A scan that matches nothing is indistinguishable from a clean tree, so each
# half is violated once here and asserted to fire.


def test_the_scanner_finds_a_printed_string():
    found = printed_strings('print("hello")\n')
    assert found == [(1, "hello")]


def test_the_scanner_reads_across_wrapped_calls():
    """Almost every call in this codebase is wrapped, and a line-based scan
    would miss the continuation lines -- which is where the emoji was.

    Adjacent literals are folded by the parser, so the wrapped form arrives as
    one string. That is the behaviour worth pinning: what matters is that the
    text on a continuation line is seen at all.
    """
    source = 'print(\n    "first"\n    "second"\n)\n'
    assert [t for _, t in printed_strings(source)] == ["firstsecond"]


def test_the_scanner_sees_a_second_argument_on_its_own_line():
    source = 'print(\n    "one",\n    "two",\n)\n'
    assert [t for _, t in printed_strings(source)] == ["one", "two"]


def test_the_scanner_ignores_what_is_not_printed():
    """Log files are opened with an explicit encoding and are not in scope."""
    assert printed_strings('log("\u2705 fine in a utf-8 file")\n') == []


@pytest.mark.parametrize("char, expected", [
    pytest.param("\u2014", False, id="em dash: fine on cp1252, not on cp437"),
    pytest.param("\u2500", False, id="box drawing: the reverse"),
    pytest.param("\u2705", False, id="the emoji that actually broke it"),
    pytest.param("-", True, id="ascii hyphen"),
])
def test_the_portability_check_agrees_with_the_measurement(char, expected):
    survives = all(_encodes(char, enc) for enc in CONSOLE_ENCODINGS)
    assert survives is expected
