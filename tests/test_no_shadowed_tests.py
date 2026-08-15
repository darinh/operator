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


def asserts_nothing(source: str) -> "list[str]":
    """Tests whose body can produce no verdict: no `assert`, no expected raise.

    The fourth way to leave no result behind, and the one that arrives by
    editing rather than by naming. A test written correctly loses its
    assertions to a careless replace, and what is left still runs, still
    exercises the code, and still prints a dot. The guard it was covering is
    gone and the suite says everything is fine.

    That happened here, to a test asserting that a deeply nested reply cannot
    crash the reply reader: an edit replaced the block and dropped the last
    three lines with it. Nothing in the run could have said so -- the *mutation*
    control found it, by surviving.

    A call to `pytest.raises`, `pytest.warns`, `pytest.fail` or a bare `raise`
    counts, and so does a call to anything named `assert_*`: a function whose
    name is a promise to raise is a verdict in the same sense, and this suite
    uses `assert_no_unattributed_authority` that way deliberately. A call to
    any *other* helper that asserts internally does not count, and that is
    deliberate too: this is a cheap syntactic check, so it over-reports rather
    than reasoning about what a helper does. Give such a test one assertion of
    its own on the value the helper returns.
    """
    verdictless: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not node.name.startswith("test_"):
            continue
        found = False
        for inner in ast.walk(node):
            if isinstance(inner, (ast.Assert, ast.Raise)):
                found = True
                break
            name = ""
            if isinstance(inner, ast.Call):
                target = inner.func
                if isinstance(target, ast.Attribute):
                    name = target.attr
                elif isinstance(target, ast.Name):
                    name = target.id
            if (name in ("raises", "warns", "fail", "xfail", "deprecated_call")
                    or name.startswith("assert")):
                found = True
                break
        if not found:
            verdictless.append(node.name)
    return verdictless


def test_no_test_can_pass_without_producing_a_verdict():
    offenders = []
    for path in _test_files():
        for name in asserts_nothing(path.read_text(encoding="utf-8")):
            offenders.append(f"{path.name}: {name}")
    assert offenders == [], (
        "these tests assert nothing, so they pass for every implementation "
        "including the broken one:\n  " + "\n  ".join(offenders)
    )


def test_the_verdictless_detector_fires():
    """Positive control, on the exact shape that got past review here."""
    source = ("def test_a(tmp_path):\n"
              "    (tmp_path / 'x').write_text('data')\n")
    assert asserts_nothing(source) == ["test_a"]


@pytest.mark.parametrize("body", [
    "    assert 1 == 1\n",
    "    with pytest.raises(ValueError):\n        int('x')\n",
    "    for x in (1, 2):\n        assert x\n",
    "    if True:\n        raise AssertionError('no')\n",
    "    assert_no_unattributed_authority('text')\n",
])
def test_the_verdictless_detector_accepts_a_real_verdict(body):
    """Negative controls: a detector that reports every test is not a detector,
    and the nested cases are the ones a body-only scan would miss."""
    assert asserts_nothing("def test_a():\n" + body) == []


def test_the_verdictless_detector_ignores_helpers():
    """Only `test_`-named functions. A helper is allowed to just build data."""
    assert asserts_nothing("def build(tmp):\n    return 1\n") == []


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
