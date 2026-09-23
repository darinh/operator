"""`--` is split in one place. Generated flags go in front of it."""
from __future__ import annotations

import ast
from pathlib import Path

from argtail import before_terminator

REPO = Path(__file__).resolve().parent.parent
KERNEL = REPO / "operator_kernel"
CLI = REPO / "operator_cli"


def test_before_terminator_inserts_extra_in_front_of_the_tail():
    assert before_terminator(
        ["--yolo", "--", "some text"], ["-i", "preamble"]
    ) == ["--yolo", "-i", "preamble", "--", "some text"]


def test_before_terminator_without_a_terminator_appends():
    assert before_terminator(["--yolo"], ["-i", "preamble"]) == [
        "--yolo", "-i", "preamble",
    ]


def test_only_argtail_splits_on_the_terminator():
    """A fifth parser that walks `--` itself is the defect this exists to stop."""
    offenders = []
    for path in sorted(KERNEL.glob("*.py")) + sorted(CLI.glob("*.py")):
        if path.stem == "argtail":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Compare):
                for comp in node.comparators:
                    if (isinstance(comp, ast.Constant) and comp.value == "--"):
                        offenders.append(f"{path.name}:{node.lineno}")
            if isinstance(node, ast.Call):
                func = node.func
                if (isinstance(func, ast.Attribute) and func.attr == "index"
                        and node.args and isinstance(node.args[0], ast.Constant)
                        and node.args[0].value == "--"):
                    offenders.append(f"{path.name}:{node.lineno}")
    assert offenders == [], (
        "these walk `--` themselves instead of at_dashdash/before_terminator:\n  "
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
