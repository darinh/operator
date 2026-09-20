"""operator_bench stays a sibling package with a closed import surface."""
from __future__ import annotations

import ast
import sys
from pathlib import Path

from test_kernel_boundary import (
    CLI, EXTENSIONS, FLEET, KERNEL, MAX_MODULE_CODE_LINES, MAX_MODULE_LINES,
    REPO, code_lines, imported_names,
)
from operator_bench.score import Scorecard

BENCH = REPO / "operator_bench"
MAX_BENCH_CODE_LINES = 900
STDLIB = sys.stdlib_module_names
OWN = {"operator_bench"}
CHILD_EXTRA = STDLIB | OWN | {
    "config", "instance", "mux", "supervisor", "launch", "breakers",
    "evidence", "probes", "paths",
}
OBSERVE_EXTRA = STDLIB | OWN | {"operator_fleet", "ledger_tail", "operator_kernel"}


def bench_modules() -> list[Path]:
    return sorted(p for p in BENCH.glob("*.py"))


def test_there_are_bench_modules_to_check():
    names = {p.name for p in bench_modules()}
    assert names >= {
        "__init__.py", "__main__.py", "child.py", "observe.py", "scenario.py",
        "score.py", "suite.py", "world.py",
    }


def test_operator_bench_import_surface():
    offenders = []
    for path in bench_modules():
        allowed = STDLIB | OWN
        if path.name == "child.py":
            allowed = CHILD_EXTRA
        elif path.name == "observe.py":
            allowed = OBSERVE_EXTRA
        for name in imported_names(path.read_text(encoding="utf-8")):
            if name not in allowed:
                offenders.append(f"{path.name}: {name}")
    assert offenders == [], "operator_bench imported outside its surface:\n  " + "\n  ".join(offenders)


def test_production_packages_do_not_import_operator_bench():
    offenders = []
    roots = (KERNEL, FLEET, CLI, EXTENSIONS)
    for root in roots:
        for path in root.glob("*.py"):
            if "operator_bench" in imported_names(path.read_text(encoding="utf-8")):
                offenders.append(str(path.relative_to(REPO)))
    assert offenders == []


def test_operator_bench_does_not_import_tests():
    offenders = []
    for path in bench_modules():
        names = imported_names(path.read_text(encoding="utf-8"))
        if "tests" in names or any(n.startswith("test_") for n in names):
            offenders.append(path.name)
    assert offenders == []


def test_bench_budgets():
    total = 0
    oversize = []
    for path in bench_modules():
        src = path.read_text(encoding="utf-8")
        code = code_lines(src)
        lines = len(src.splitlines())
        total += code
        if code > MAX_MODULE_CODE_LINES or lines > MAX_MODULE_LINES:
            oversize.append(f"{path.name}: {code} code, {lines} total")
    assert oversize == [], "\n  ".join(oversize)
    assert total <= MAX_BENCH_CODE_LINES, (
        f"operator_bench is {total} code lines, budget {MAX_BENCH_CODE_LINES}"
    )


def test_scorecard_has_no_combined_score_field():
    names = set(Scorecard.__dataclass_fields__)
    for banned in ("accuracy", "score", "pass_rate"):
        assert banned not in names
    assert "miss_rate" in names
    assert "false_alarm_rate" in names
    assert "spend_recorded_coverage" in names
    assert "spend_ceiling_fidelity" in names


def test_printed_cli_text_is_ascii():
    source = (BENCH / "__main__.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    texts = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = getattr(node.func, "id", "") or getattr(node.func, "attr", "")
        if name != "print":
            continue
        for sub in ast.walk(node):
            if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
                texts.append(sub.value)
    assert texts
    for text in texts:
        text.encode("ascii")
        assert text.isascii()
