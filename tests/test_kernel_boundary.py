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
#:
#: Total lines, because this one is about *navigability*: a file nobody can
#: scroll is hard to work in whatever it is made of. The complexity budget
#: below counts something different, and the difference is deliberate.
MAX_MODULE_LINES = 800

#: The complexity budget, in **code** lines -- docstrings, comments and blanks
#: excluded.
#:
#: It counted total lines until it fired on a change that added an authority
#: composer, and the cheapest way to pass it was to delete explanation. That is
#: the most damaging edit available in this repository, so the metric was
#: measuring the wrong thing and creating exactly the wrong pressure.
#:
#: Measured before changing it, because "the guard fired so I changed the
#: guard" is the shape of suppressing a check and deserves evidence rather than
#: an argument. The kernel is 52% prose and blank lines; `copilot_operator.py`
#: -- the 9,120-line module this budget exists to prevent -- is 43%. That gap
#: is real but modest, and on its own it would be a thin reason. The load-
#: bearing reason is the direction of the pressure: the hazard this constant
#: names is *complexity*, and a total-line budget charges a page of reasoning
#: at the same rate as a page of branching, so the first move it rewards is
#: deleting the reasoning. On the honest measure the extraction is further
#: along than the total suggested -- 3,607 code lines against the old module's
#: 5,227, where the totals are 7,547 against 9,120.
#:
#: Set at 3,607 measured plus the headroom the original carried, converted
#: rather than reinvented: 7,000 over the spike's ~6,000 is 1,000 total lines,
#: which at the kernel's measured 52% prose is about 480 code lines. Note this
#: is *tighter* than what it replaces, not looser -- prose no longer consumes
#: budget, so every line that does is one the kernel has to justify.
#:
#: **If this needs raising, cut before you raise, and cut here first:** the
#: project catalogue (`projects_root`, `project_dir`, `catalog_rows`,
#: `guid_is_usable`) exists to resolve one handoff path and one working
#: directory. Both become arguments the caller passes once continuity moves to
#: the ledger, and roughly 250 lines leave with them.
MAX_KERNEL_CODE_LINES = 4100

#: Per-module code ceiling, the same split applied one file down.
#: `supervisor.py` is the largest at 446.
MAX_MODULE_CODE_LINES = 500


def code_lines(source: str) -> int:
    """Lines that are neither docstring, comment, nor blank.

    Counted over a *set* of docstring line numbers rather than by summing each
    docstring's length. The first draft summed, and double-charged every blank
    line inside a docstring -- once as prose and once as blank -- which made
    heavily documented modules score negative. It also made the kernel's
    measured size an underestimate, so the budget derived from it would have
    been set too low.

    Docstrings are found through `ast`, not by counting quotes: a module that
    assigns a triple-quoted string to a variable is holding data, not
    documenting itself, and should be charged for it.
    """
    tree = ast.parse(source)
    doc_lines: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.FunctionDef,
                                 ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        body = getattr(node, "body", None)
        if not body:
            continue
        first = body[0]
        if (isinstance(first, ast.Expr)
                and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)):
            doc_lines.update(range(first.lineno, (first.end_lineno or
                                                  first.lineno) + 1))
    counted = 0
    for number, line in enumerate(source.splitlines(), start=1):
        if number in doc_lines:
            continue
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        counted += 1
    return counted

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
    total = sum(code_lines(p.read_text(encoding="utf-8"))
                for p in kernel_modules())
    assert total <= MAX_KERNEL_CODE_LINES, (
        f"kernel is {total} code lines, budget {MAX_KERNEL_CODE_LINES}. Raising "
        f"this number is a decision about what a kernel is, not a formality — "
        f"and the cut to make first is named beside the constant."
    )


def test_no_kernel_module_exceeds_the_code_ceiling():
    oversize = [
        f"{p.name}: {code_lines(p.read_text(encoding='utf-8'))} code lines"
        for p in kernel_modules()
        if code_lines(p.read_text(encoding="utf-8")) > MAX_MODULE_CODE_LINES
    ]
    assert oversize == [], (
        f"over the {MAX_MODULE_CODE_LINES}-line module code ceiling:\n  "
        + "\n  ".join(oversize)
    )


# --- controls for the budget's metric ---------------------------------------

def test_the_budget_charges_for_code():
    """The positive control. Without it the budget could count nothing."""
    before = code_lines("def f():\n    return 1\n")
    after = code_lines("def f():\n" + "    x = 1\n" * 40 + "    return 1\n")
    assert after - before == 40


def test_the_budget_does_not_charge_for_explanation():
    """The reason the metric changed, asserted rather than argued.

    If this fails, the budget is once again pressuring whoever hits it to
    delete the reasoning instead of the complexity, which is the failure that
    caused the change.
    """
    bare = "def f():\n    return 1\n"
    documented = 'def f():\n    """' + "\nwhy\n" * 60 + '"""\n    return 1\n'
    assert code_lines(documented) == code_lines(bare)


def test_the_budget_does_charge_for_string_data():
    """A triple-quoted string that is not a docstring is data, not reasoning.

    The control that stops the exemption above being widened into a hole: were
    docstrings detected by counting quote characters instead of by parsing, a
    module could park arbitrary payload in a string and pay nothing for it.
    """
    data = 'PAYLOAD = """' + "\nrow\n" * 30 + '"""\n'
    assert code_lines(data) > 30


def test_the_budget_does_not_charge_for_comments_or_blanks():
    assert code_lines("# a\n\n# b\n\nx = 1\n") == 1


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
