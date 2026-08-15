"""The kernel boundary, enforced from the first commit.

The system this replaces reached 28,443 lines with one module at 9,120, and the
design review's blocking criticism of the first plan was that nothing prevented
the new one re-accumulating the same way. Kill criteria fire after months;
discipline is what a tired agent at 2am does not have. So the boundary is a test.

Three rules, each with a positive control that violates it and a negative
control that shows the correct spelling still passes. A detector that matches
nothing reports the whole tree clean, which reads exactly like success.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
KERNEL = REPO / "operator_kernel"

#: What the kernel may import beyond the standard library and itself. Empty on
#: purpose: a supervision kernel that needs a third-party package has stopped
#: being a kernel. Adding a name here is a decision somebody has to defend in
#: review, which is the point of it being a list rather than a convention.
ALLOWED_THIRD_PARTY: frozenset[str] = frozenset()

#: Modules the kernel is defined as *not* containing. These are the concerns
#: that grew around the old supervisor and buried it: metrics, project
#: instructions, backlog, mail, conversation capture, spec scaffolding. The
#: kernel may not import them, and the repo may not acquire them without this
#: list being edited deliberately.
FORBIDDEN = frozenset({
    "operator_ingest", "copilot_operator", "project_paths", "project_features",
    "project_instructions", "backlog_tool", "handoff_tool", "conversation_log",
    "conversation_viewer", "operator_mail", "mail_affiliation", "setup_tools",
    "operator_session", "operator_work", "operator_worktree",
})

#: The ceiling on a single kernel module. `copilot_operator.py` reached 9,120
#: lines one reasonable-looking addition at a time; no single commit was the
#: problem. A number here turns "this file is getting big" from a judgement
#: nobody makes into a failure somebody has to answer for.
MAX_MODULE_LINES = 800

#: The ceiling on the kernel as a whole.
#:
#: 7000 first, from the extraction spike's measurement of ~6000 plus room.
#: Raised once, to 7500, when `seat.py` took it to 7022 -- and the raise was
#: made only after checking for fat and not finding any: the project catalogue
#: helpers in `paths.py` look like they do not belong until you follow them to
#: `crash_recovery_verdict`, which needs the handoff path, and to the
#: supervisor, which needs the primary checkout. Identity is real supervision
#: surface too; the kernel decides who commits, and backlog 0013 is what happens
#: when nothing does.
#:
#: **If this needs raising again, cut before you raise, and cut here first:**
#: the project catalogue (`projects_root`, `project_dir`, `catalog_rows`,
#: `guid_is_usable`) exists to resolve one handoff path and one working
#: directory. Both become arguments the caller passes once continuity moves to
#: the ledger, and roughly 250 lines leave with them.
MAX_KERNEL_LINES = 7500


def kernel_modules() -> list[Path]:
    return sorted(KERNEL.glob("*.py"))


def _module_names() -> set[str]:
    return {p.stem for p in kernel_modules()}


def imported_names(source: str) -> set[str]:
    """Top-level module names imported by ``source``, however spelled."""
    names: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            # `level` non-zero is a relative import, which names nothing outside.
            if node.level == 0 and node.module:
                names.add(node.module.split(".")[0])
    return names


def test_there_are_kernel_modules_to_check():
    """A scan over an empty directory reports a clean tree."""
    assert len(kernel_modules()) >= 5


def test_the_kernel_imports_nothing_it_is_defined_as_not_being():
    import sys

    stdlib = sys.stdlib_module_names
    ours = _module_names()
    offenders: list[str] = []
    for path in kernel_modules():
        for name in imported_names(path.read_text(encoding="utf-8")):
            if name in FORBIDDEN:
                offenders.append(f"{path.name}: {name} (forbidden)")
            elif name not in stdlib and name not in ours and name not in ALLOWED_THIRD_PARTY:
                offenders.append(f"{path.name}: {name} (undeclared)")
    assert offenders == [], (
        "the kernel reached outside itself:\n  " + "\n  ".join(offenders)
        + "\n\nThe kernel supervises processes. If it needs one of these, either "
        "the dependency belongs on the other side of the boundary, or the "
        "boundary moved and this list should say so explicitly."
    )


def test_no_kernel_module_exceeds_the_line_ceiling():
    oversize = [
        f"{p.name}: {len(p.read_text(encoding='utf-8').splitlines())} lines"
        for p in kernel_modules()
        if len(p.read_text(encoding="utf-8").splitlines()) > MAX_MODULE_LINES
    ]
    assert oversize == [], (
        f"over the {MAX_MODULE_LINES}-line module ceiling:\n  "
        + "\n  ".join(oversize)
        + "\n\nSplit it or move it out. The module this kernel replaces reached "
        "9,120 lines and no single commit was the problem."
    )


def test_the_kernel_as_a_whole_stays_under_its_budget():
    total = sum(len(p.read_text(encoding="utf-8").splitlines())
                for p in kernel_modules())
    assert total <= MAX_KERNEL_LINES, (
        f"kernel is {total} lines, budget {MAX_KERNEL_LINES}. Raising this "
        f"number is a decision about what a kernel is, not a formality."
    )


# ── controls ────────────────────────────────────────────────────
#
# Each detector is violated once here and asserted to fire, and the correct
# spelling is asserted to pass. Without both, a scan that matches nothing is
# indistinguishable from a clean tree.
@pytest.mark.parametrize("source, expected", [
    pytest.param("import operator_ingest\n", {"operator_ingest"}, id="plain import"),
    pytest.param("from operator_ingest import connect\n", {"operator_ingest"},
                 id="from-import"),
    pytest.param("import operator_ingest.thing\n", {"operator_ingest"},
                 id="dotted import"),
    pytest.param("from operator_ingest.sub import x\n", {"operator_ingest"},
                 id="dotted from-import"),
    pytest.param("def f():\n    import operator_ingest\n", {"operator_ingest"},
                 id="lazy import inside a function"),
    pytest.param("import os, operator_ingest\n", {"os", "operator_ingest"},
                 id="comma import"),
    pytest.param("from . import sibling\n", set(), id="relative names nothing outside"),
    pytest.param("import sqlite3\n", {"sqlite3"}, id="stdlib is seen but allowed"),
])
def test_the_import_scan_sees_every_spelling(source, expected):
    assert imported_names(source) == expected


def test_the_forbidden_list_is_not_vacuous():
    """A forbidden list naming nothing real forbids nothing.

    Every name here must be a module of the system being extracted from, or the
    entry is a guess that will go on reading like a considered decision.
    """
    old = Path(r"C:\Users\darin\repos\copilot-tools")
    if not old.exists():          # the source repo is gone; nothing to check against
        pytest.skip("source repository not present")
    existing = {p.stem for p in old.glob("*.py")}
    unreal = sorted(FORBIDDEN - existing)
    assert unreal == [], f"FORBIDDEN names modules that do not exist: {unreal}"
