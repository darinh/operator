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
import io
import os
import tokenize
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

#: The kernel-wide *total*-line ceiling, for navigability rather than
#: complexity. Restored after the switch to code lines deleted it, leaving
#: overall size uncapped -- so "tighter, not looser" was true of the code
#: measure and silent about totals. Generous, because prose is welcome here;
#: present, because "no ceiling at all" is not a decision anyone made.
MAX_KERNEL_TOTAL_LINES = 9000

#: Per-module code ceiling, the same split applied one file down.
#: `supervisor.py` is the largest at 446.
MAX_MODULE_CODE_LINES = 500


def code_lines(source: str) -> int:
    """Lines carrying any code token: not docstring, comment, or blank.

    Tokenised rather than matched line by line. The first draft asked whether a
    stripped line started with ``#``, which is true of every line of a
    triple-quoted payload whose content happens to be comment-shaped -- so a
    100-line embedded data blob scored 2, and the control missed it because its
    fixture used the word ``row``. A budget that can be zeroed by choosing the
    right filler is not a budget.

    Docstring *spans* come from the AST, because "the first statement of a
    module, class or function, and a string" is a structural fact no tokeniser
    knows. Everything else is decided by whether a line contains a token that
    is not a comment, a newline, or indentation bookkeeping -- so
    ``\"\"\"doc\"\"\"; x = 1`` counts as the code line it is, rather than
    vanishing into its docstring's span.
    """
    tree = ast.parse(source)
    doc_tokens: set[tuple[int, int]] = set()
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
            # Keyed on the exact start position, not the line span. Keying on
            # lines skips *every* token sharing the docstring's last line,
            # which silently swallowed the `x = 1` in `"""doc"""; x = 1` and
            # made the control for that case fail in the direction that looks
            # like the code is fine.
            doc_tokens.add((first.value.lineno, first.value.col_offset))

    counted: set[int] = set()
    skip = {tokenize.COMMENT, tokenize.NL, tokenize.NEWLINE, tokenize.INDENT,
            tokenize.DEDENT, tokenize.ENDMARKER, tokenize.ENCODING}
    reader = io.StringIO(source).readline
    for token in tokenize.generate_tokens(reader):
        if token.type in skip or not token.string.strip():
            continue
        if token.start in doc_tokens:
            continue
        counted.update(range(token.start[0], token.end[0] + 1))
    return len(counted)

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


def test_the_kernel_does_not_use_forbidden_modules_it_never_imported():
    """A forbidden name used without importing it is invisible to an import scan.

    `test_the_kernel_imports_nothing_it_should_not` reads `import` statements,
    so a module referenced as a bare name passes it silently -- and the guard
    then reports the tree clean while the violation is present, which is the
    exact failure mode the module docstring warns about.

    It was present. `preamble.py` and `supervisor.py` both called
    `operator_session`, which is on `FORBIDDEN` and was imported by neither, so
    every call was a latent `NameError`. In `supervisor.py` it was worse than
    latent: `_loop_work_db` and `_loop_start_session` each swallow `Exception`
    and return `None`, so the `NameError` was caught and the whole
    work-assignment subsystem returned "no assignment" forever -- indis-
    tinguishable from a session that genuinely had none. FR-2 says the
    assignment reaches the agent before its first token; it never did.
    """
    offenders = []
    for path in kernel_modules():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        bound = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    bound.add((alias.asname or alias.name).split(".")[0])
            elif isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    bound.add(alias.asname or alias.name)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                                   ast.ClassDef)):
                bound.add(node.name)
            elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
                bound.add(node.id)
        used = {node.id for node in ast.walk(tree)
                if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)}
        for ghost in sorted((used & FORBIDDEN) - bound):
            offenders.append(f"{path.name}: {ghost}")
    assert offenders == [], (
        "the kernel calls forbidden modules it never imported, so every call "
        "is a NameError waiting for its branch to be taken:\n  "
        + "\n  ".join(offenders)
        + "\n\nEither the dependency is injected by the caller, or the code "
        "using it does not belong in the kernel."
    )


def test_the_ghost_name_detector_fires(tmp_path):
    """Positive control, on synthetic source rather than the tree.

    Scoring against the real tree would make this pass the moment the tree is
    clean, which is precisely when a detector's silence stops being evidence.
    """
    source = "def f():\n    return operator_session.describe(1)\n"
    tree = ast.parse(source)
    used = {n.id for n in ast.walk(tree)
            if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}
    assert used & FORBIDDEN == {"operator_session"}


def test_the_ghost_name_detector_accepts_a_bound_name():
    """Negative control: a name that *is* bound must not be reported.

    Without this the detector could report every use of anything and still
    look like it worked.
    """
    source = "operator_session = make_stub()\nx = operator_session.describe(1)\n"
    tree = ast.parse(source)
    bound = {n.id for n in ast.walk(tree)
             if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store)}
    used = {n.id for n in ast.walk(tree)
            if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}
    assert (used & FORBIDDEN) - bound == set()


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


def test_the_budget_charges_for_comment_shaped_string_data():
    """The control that the one above was too polite to be.

    Its fixture said `row`, so a line-based reader that treated any line
    starting with `#` as a comment passed it -- while a payload of
    comment-shaped lines scored 2 instead of 100. A budget that can be zeroed
    by choosing the right filler is not a budget, and the only thing standing
    between those two fixtures was the word I happened to pick.
    """
    data = 'PAYLOAD = """' + "\n# looks like a comment\n" * 50 + '"""\n'
    assert code_lines(data) > 50


def test_the_budget_charges_a_line_that_shares_a_docstring_line():
    """`\"\"\"doc\"\"\"; x = 1` is a line of code and must be charged as one."""
    assert code_lines('def f():\n    """doc"""; x = 1\n') == 2


def test_the_budget_does_not_charge_for_comments_or_blanks():
    assert code_lines("# a\n\n# b\n\nx = 1\n") == 1


def test_no_kernel_module_exceeds_the_line_ceiling():
    """Total lines, for navigability. Distinct from the code budget.

    Kept explicitly, because when the kernel-wide budget switched to code lines
    the kernel-wide *total* ceiling was deleted with it and nothing capped
    overall size any more. A reviewer caught that, and caught that the commit
    describing the change as "tighter, not looser" was therefore true of code
    and silent about totals.
    """
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


def test_the_kernel_as_a_whole_stays_under_its_total_ceiling():
    """The navigability ceiling for the whole kernel, restored.

    Generous on purpose -- prose is welcome and this is not the complexity
    budget -- but not absent, which is what it briefly became.
    """
    total = sum(len(p.read_text(encoding="utf-8").splitlines())
                for p in kernel_modules())
    assert total <= MAX_KERNEL_TOTAL_LINES, (
        f"kernel is {total} total lines, ceiling {MAX_KERNEL_TOTAL_LINES}."
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

    The source repository is located relative to this one rather than by
    absolute path. The absolute spelling worked on exactly one machine and
    would have skipped -- silently, reading as a pass -- on any clone or CI
    runner, which is the whole failure this file exists to talk about.

    `COPILOT_TOOLS_REPO` overrides it, so a checkout that lives elsewhere can
    still be checked instead of skipped.
    """
    override = os.environ.get("COPILOT_TOOLS_REPO")
    if override:
        candidates = [Path(override)]
    else:
        # Both spellings, because this file is run from the primary checkout
        # *and* from linked worktrees under `.worktrees/`, which sit one level
        # deeper. Getting this wrong does not fail -- it skips, which reads as
        # a pass, which is how the absolute path survived as long as it did.
        candidates = [REPO.parent / "copilot-tools",
                      REPO.parent.parent / "copilot-tools"]
    found = [path for path in candidates if path.exists()]
    if not found:
        pytest.skip("source repository not present at "
                    + " or ".join(str(c) for c in candidates))
    old = found[0]
    existing = {p.stem for p in old.glob("*.py")}
    unreal = sorted(FORBIDDEN - existing)
    assert unreal == [], f"FORBIDDEN names modules that do not exist: {unreal}"
