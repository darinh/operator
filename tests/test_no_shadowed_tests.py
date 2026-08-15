"""No test in this suite may be silently unrunnable.

Three ways a test produces no result at all, and the reason they are worth a
guard of their own: a failing test is a red line in the output, but a test that
never runs is *indistinguishable from a test that passed*. The suite reports
success and the guarantee is gone. Mutation testing barely helps — a shadowed
duplicate of an identical test leaves no surviving mutant behind, because the
copy still covers the behaviour.

This was written the same afternoon it was needed. Adding a whole-kernel total
ceiling to `test_kernel_boundary.py`, I gave the new function the name of one
already in the file. Python bound the second over the first, pytest collected
one, and the suite went green with a guard silently retired. Nothing in the run
could have told me.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

TESTS = Path(__file__).resolve().parent


def _test_files() -> "list[Path]":
    return sorted(p for p in TESTS.rglob("test_*.py"))


def duplicate_test_names(source: str) -> "list[str]":
    """Names defined more than once at module level, later shadowing earlier.

    Module level only. A method repeated inside two different classes is two
    different tests, and reporting it would train people to ignore this.
    """
    seen: set[str] = set()
    repeated: list[str] = []
    for node in ast.parse(source).body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if node.name in seen:
            repeated.append(node.name)
        seen.add(node.name)
    return repeated


def test_no_test_file_shadows_one_of_its_own_tests():
    offenders = []
    for path in _test_files():
        for name in duplicate_test_names(path.read_text(encoding="utf-8")):
            offenders.append(f"{path.name}: {name} defined twice")
    assert offenders == [], (
        "a test name defined twice means the first is never collected, and an "
        "uncollected test is indistinguishable from a passing one:\n  "
        + "\n  ".join(offenders)
    )


def test_the_shadowing_detector_fires():
    """Positive control on synthetic source.

    Scored against a fixture rather than the tree, so it keeps meaning the
    moment the tree is clean -- which is exactly when the detector's silence
    stops being evidence of anything.
    """
    source = "def test_a():\n    pass\n\n\ndef test_a():\n    pass\n"
    assert duplicate_test_names(source) == ["test_a"]


def test_the_shadowing_detector_accepts_distinct_names():
    """Negative control: a detector that reports everything is not a detector."""
    source = "def test_a():\n    pass\n\n\ndef test_b():\n    pass\n"
    assert duplicate_test_names(source) == []


def test_the_shadowing_detector_ignores_methods_of_different_classes():
    source = ("class TestOne:\n    def test_x(self):\n        pass\n\n\n"
              "class TestTwo:\n    def test_x(self):\n        pass\n")
    assert duplicate_test_names(source) == []


def test_every_test_file_is_collectable():
    """A file that cannot be parsed contributes no tests and no failure either.

    `pytest` reports a collection error for a file it cannot import, so this is
    partly belt-and-braces -- but it also catches a file that parses and
    imports nothing, and it costs one `ast.parse`.
    """
    empty = []
    for path in _test_files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        names = [n.name for n in tree.body
                 if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                 and n.name.startswith("test_")]
        classes = [n for n in tree.body if isinstance(n, ast.ClassDef)]
        if not names and not classes:
            empty.append(path.name)
    assert empty == [], (
        "test files containing no tests:\n  " + "\n  ".join(empty)
    )
