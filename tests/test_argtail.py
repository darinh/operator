"""`--` is split in one place. Generated flags go in front of it."""
from __future__ import annotations

import ast
from pathlib import Path

from argtail import before_terminator

REPO = Path(__file__).resolve().parent.parent
KERNEL = REPO / "operator_kernel"
CLI = REPO / "operator_cli"

ARGV_NAMES = frozenset({
    "args", "argv", "user_args", "copilot_args", "launch_args",
})
EXEMPT = frozenset({
    "at_dashdash", "before_terminator", "quote_argv", "quote_one",
})


def _call_name(node):
    if not isinstance(node, ast.Call):
        return None
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _unwrap(node):
    while (isinstance(node, ast.Call)
           and _call_name(node) in {"enumerate", "list", "tuple", "iter",
                                    "reversed"}
           and node.args):
        node = node.args[0]
    return node


def _is_at_dashdash(node):
    return _call_name(node) == "at_dashdash"


def _name(node):
    return node.id if isinstance(node, ast.Name) else None


def _statements(func: ast.FunctionDef):
    stack = list(func.body)
    while stack:
        stmt = stack.pop(0)
        yield stmt
        if isinstance(stmt, ast.If):
            stack = list(stmt.body) + list(stmt.orelse) + stack
        elif isinstance(stmt, (ast.For, ast.While, ast.With)):
            stack = list(stmt.body) + stack
        elif isinstance(stmt, ast.Try):
            extra = list(stmt.body)
            for handler in stmt.handlers:
                extra.extend(handler.body)
            extra.extend(stmt.orelse)
            extra.extend(stmt.finalbody)
            stack = extra + stack


def _mark_split(stmt, raw: set[str]) -> None:
    if not isinstance(stmt, ast.Assign) or not stmt.targets:
        return
    value = stmt.value
    dest = stmt.targets[0]
    if isinstance(value, ast.Subscript) and _is_at_dashdash(value.value):
        n = _name(dest)
        if n:
            raw.discard(n)
        return
    if not _is_at_dashdash(value):
        return
    if not isinstance(dest, ast.Tuple) or not dest.elts:
        return
    n = _name(dest.elts[0])
    if n:
        raw.discard(n)


def _iterates_raw(stmt, raw: set[str]) -> str | None:
    for node in ast.walk(stmt):
        if isinstance(node, ast.For):
            n = _name(_unwrap(node.iter))
            if n in raw:
                return n
        if isinstance(node, ast.comprehension):
            n = _name(_unwrap(node.iter))
            if n in raw:
                return n
        if isinstance(node, ast.Compare):
            for i, op in enumerate(node.ops):
                if not isinstance(op, ast.In):
                    continue
                n = _name(_unwrap(node.comparators[i]))
                if n in raw:
                    return n
    return None


def functions_that_scan_the_raw_tail(source: str) -> list[str]:
    """Functions that iterate an argv parameter without splitting it first.

    Statement order is flattened through if/for/try. A split inside a branch
    that does not dominate a later scan is treated as a split. That is looser
    than dataflow. It is still enough to catch a reader that never splits,
    which is the bypass this exists for.
    """
    found = []
    tree = ast.parse(source)
    for func in ast.walk(tree):
        if not isinstance(func, ast.FunctionDef) or func.name in EXEMPT:
            continue
        raw = {a.arg for a in func.args.args if a.arg in ARGV_NAMES}
        if not raw:
            continue
        for stmt in _statements(func):
            _mark_split(stmt, raw)
            hit = _iterates_raw(stmt, raw)
            if hit:
                found.append(func.name)
                break
    return found


def test_before_terminator_inserts_extra_in_front_of_the_tail():
    assert before_terminator(
        ["--yolo", "--", "some text"], ["-i", "preamble"]
    ) == ["--yolo", "-i", "preamble", "--", "some text"]


def test_before_terminator_without_a_terminator_appends():
    assert before_terminator(["--yolo"], ["-i", "preamble"]) == [
        "--yolo", "-i", "preamble",
    ]


def test_the_raw_tail_scan_detector_fires():
    source = (
        "def has_agent_flag(args):\n"
        "    return any(a == '--agent' for a in args)\n"
    )
    assert functions_that_scan_the_raw_tail(source) == ["has_agent_flag"]


def test_the_raw_tail_scan_detector_accepts_a_split():
    source = (
        "def has_agent_flag(args):\n"
        "    args, _ = at_dashdash(args)\n"
        "    return any(a == '--agent' for a in args)\n"
    )
    assert functions_that_scan_the_raw_tail(source) == []


def test_argv_readers_do_not_scan_the_raw_tail():
    offenders = []
    for path in sorted(KERNEL.glob("*.py")) + sorted(CLI.glob("*.py")):
        for name in functions_that_scan_the_raw_tail(
                path.read_text(encoding="utf-8")):
            offenders.append(f"{path.name}:{name}")
    assert offenders == [], (
        "these iterate an argv list without at_dashdash first, so a "
        "literal after `--` is treated as an operator flag:\n  "
        + "\n  ".join(offenders)
    )


def test_generated_flags_call_before_terminator():
    launch = (KERNEL / "launch.py").read_text(encoding="utf-8")
    supervisor = (KERNEL / "supervisor.py").read_text(encoding="utf-8")
    assert "before_terminator" in launch
    assert "before_terminator" in supervisor
    assert 'argv += ["-i"' not in launch
    assert 'launch_args.append(f"--resume' not in supervisor
    assert '[*argv, "--log-level"' not in launch

