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

#: The second source package. It is not the kernel and is deliberately not
#: held to the kernel's budget, but it is this repository's code and the suite
#: may import it -- so the scan below has to know the name exists. Its own
#: rules live in `test_fleet_boundary.py`; see that file for why an extraction
#: that made lines free on the far side of a boundary would be a fiction.
FLEET = REPO / "operator_fleet"

#: What the kernel may import beyond the standard library and itself. Empty on
#: purpose: a supervision kernel that needs a third-party package has stopped
#: being a kernel. Adding a name here is a decision somebody has to defend in
#: review, which is the point of it being a list rather than a convention.
ALLOWED_THIRD_PARTY: frozenset[str] = frozenset()

#: What the *test suite* may import beyond the standard library, the kernel and
#: its own modules. `pytest` and nothing else: the suite's dependency list is a
#: place a convenience import becomes a requirement nobody declared, and the
#: kernel's whole claim is that it runs with a stdlib Python.
ALLOWED_TEST_THIRD_PARTY: frozenset[str] = frozenset({"pytest"})

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
#:
#: That rule has been followed once already, and the headroom below the two
#: numbers is what it bought rather than slack anyone left. The kernel stood at
#: 4,091 and exactly 9,000 with nothing further able to land; `snapshot.py`
#: moved to `operator_fleet/` (60 code, 106 total) because describing a fleet
#: is not supervising one and no kernel module imported it. A cut only counts
#: if the lines are not free where they land, so `test_fleet_boundary.py`
#: charges for them there.
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


def fleet_modules() -> list[Path]:
    return sorted(FLEET.glob("*.py"))


def _module_names() -> set[str]:
    return {p.stem for p in kernel_modules()}


def _fleet_module_names() -> set[str]:
    return {p.stem for p in fleet_modules()}


def imported_names(source: str) -> set[str]:
    """Top-level module names imported by ``source``, however spelled.

    Literal dynamic imports count. `__import__("copilot_operator")` and
    `importlib.import_module("operator_liveness")` are the two forms that
    reproduce the exact defect this scan exists to catch while leaving no
    `Import` node behind, so a scan that reads only import *statements* reports
    the tree clean while the violation is present -- the failure mode
    `test_the_kernel_does_not_use_forbidden_modules_it_never_imported` was
    written for, in a different disguise.

    Only literal string arguments are read. A computed module name cannot be
    resolved statically, and guessing at one would produce false accusations
    that are worse than the silence: this scan's whole value is that a failure
    means something.
    """
    names: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            # `level` non-zero is a relative import, which names nothing outside.
            if node.level == 0 and node.module:
                names.add(node.module.split(".")[0])
        elif isinstance(node, ast.Call):
            target = _dynamic_import_argument(node)
            if target:
                names.add(target.split(".")[0])
    return names


def _dynamic_import_argument(node: ast.Call) -> str | None:
    """The literal module name in `__import__(...)`/`importlib.import_module(...)`."""
    func = node.func
    if isinstance(func, ast.Name):
        called = func.id
    elif isinstance(func, ast.Attribute):
        called = func.attr
    else:
        return None
    if called not in ("__import__", "import_module"):
        return None
    if not node.args:
        return None
    first = node.args[0]
    if isinstance(first, ast.Constant) and isinstance(first.value, str):
        return first.value
    return None


def test_there_are_kernel_modules_to_check():
    """A scan over an empty directory reports a clean tree."""
    assert len(kernel_modules()) >= 5


def test_the_kernel_imports_nothing_it_is_defined_as_not_being():
    import sys

    stdlib = sys.stdlib_module_names
    ours = _module_names()
    fleet = _fleet_module_names()
    offenders: list[str] = []
    for path in kernel_modules():
        for name in imported_names(path.read_text(encoding="utf-8")):
            if name in FORBIDDEN:
                offenders.append(f"{path.name}: {name} (forbidden)")
            elif name in fleet:
                # Named separately because "undeclared" would read as an
                # oversight in this list. It is not: the arrow between these
                # two packages points one way on purpose, and `snapshot` left
                # the kernel precisely because nothing in it imported that
                # file. A kernel module importing one now would make the
                # extraction a rename.
                offenders.append(f"{path.name}: {name} (fleet, not kernel)")
            elif name not in stdlib and name not in ours and name not in ALLOWED_THIRD_PARTY:
                offenders.append(f"{path.name}: {name} (undeclared)")
    assert offenders == [], (
        "the kernel reached outside itself:\n  " + "\n  ".join(offenders)
        + "\n\nThe kernel supervises processes. If it needs one of these, either "
        "the dependency belongs on the other side of the boundary, or the "
        "boundary moved and this list should say so explicitly."
    )


def suite_modules() -> list[Path]:
    """Every checked-in test module, `tests/pending/` included.

    Pending is scanned even though it is not collected. A test in there is
    source this repository ships, and the moment somebody moves one back it is
    live -- so excluding it would put the hole exactly where the next port
    lands.
    """
    return sorted((REPO / "tests").rglob("*.py"))


def _importable_suite_names() -> set[str]:
    """Test modules importable as top-level names.

    Only `tests/` itself is on `pythonpath` (see pyproject), so
    `tests/pending/test_restart_all_loops.py` does NOT make
    `test_restart_all_loops` importable. Counting it as local would let an
    outside module of that name through the scan below on the strength of a
    nested file that shares its stem -- accepting a stranger because somebody
    unrelated has the same name.
    """
    return {p.stem for p in (REPO / "tests").glob("*.py")}


def test_the_test_suite_imports_nothing_from_outside_this_repository():
    """The boundary applies to the suite, and it was the suite that broke it.

    `test_the_kernel_imports_nothing_it_is_defined_as_not_being` scans
    `operator_kernel/` only, so `tests/` was outside every guard in this file --
    and `tests/` is where the violation was. Two of them, both invisible:

    * `conftest.py` did `import copilot_operator`, a name on FORBIDDEN, and
      substituted the multiplexer fake into *its* module attribute. The kernel
      reads `config.MUX`, so the substitution was inert for the whole life of
      the extraction while 289 tests passed.
    * `test_loop_pid_identity.py` did `import operator_liveness`, and nine
      assertions in it graded the OLD repository's module rather than the
      `process_identity` it was moved here to verify.

    Neither could fail. `copilot-tools` is installed as an editable package on
    the extracting developer's machine, so both names resolve -- to
    `../copilot-tools/*.py`. On a fresh clone or a CI runner they resolve to
    nothing and the whole suite dies at collection, which is the same defect
    wearing its loud face: 289 tests reported as passing here were 289 tests
    that could not run anywhere else.

    This is a static scan of import statements rather than a check on
    `sys.modules`, because the failure is that the wrong module IMPORTS
    cleanly. Asking the interpreter what it loaded gets a confident answer
    about the wrong file.
    """
    import sys

    stdlib = sys.stdlib_module_names
    kernel = _module_names()
    fleet = _fleet_module_names()
    local = _importable_suite_names()
    offenders: list[str] = []
    for path in suite_modules():
        for name in imported_names(path.read_text(encoding="utf-8")):
            if name in FORBIDDEN:
                offenders.append(
                    f"{path.relative_to(REPO)}: {name} (forbidden)")
            elif (name not in stdlib and name not in kernel
                    and name not in fleet
                    and name not in local
                    and name not in ALLOWED_TEST_THIRD_PARTY):
                offenders.append(
                    f"{path.relative_to(REPO)}: {name} (undeclared)")
    assert offenders == [], (
        "the test suite reached outside this repository:\n  "
        + "\n  ".join(offenders)
        + "\n\nA suite that imports its subject from another checkout reports "
        "on that checkout. If the name is the kernel's under an older "
        "spelling, take it from the `op` namespace, which aliases them."
    )


def test_there_are_suite_modules_to_check():
    """Otherwise the scan above passes by finding no files."""
    assert len(suite_modules()) >= 5
    assert len(_importable_suite_names()) >= 5


def test_only_top_level_test_modules_count_as_importable():
    """`tests/pending/` is scanned, but its stems are not importable names.

    Both halves matter and they pull in opposite directions, which is why they
    are pinned together: a pending file must be READ by the scan, and its name
    must not be accepted as a local module by it.
    """
    scanned = {p.name for p in suite_modules()}
    assert "test_restart_all_loops.py" in scanned, (
        "the scan no longer reads tests/pending/, which is where the next "
        "cross-repository import will arrive"
    )
    assert "test_restart_all_loops" not in _importable_suite_names(), (
        "a nested test stem is being treated as an importable top-level name, "
        "so an outside module of that name would pass the scan"
    )


def test_the_suite_import_scan_would_catch_the_import_that_prompted_it():
    """Positive control, on synthetic source.

    Scored against the tree, this test starts passing the moment the tree is
    clean -- which is precisely when a detector's silence stops being evidence.
    The two spellings below are the two that were actually present.
    """
    assert imported_names("import copilot_operator") & FORBIDDEN == {
        "copilot_operator"}
    found = imported_names("import operator_liveness as ol")
    assert found == {"operator_liveness"}
    assert "operator_liveness" not in _module_names(), (
        "operator_liveness is now a kernel module name, so the scan would "
        "accept the bare import; this control needs rewriting"
    )


@pytest.mark.parametrize("source", [
    'x = __import__("copilot_operator")',
    'import importlib\nx = importlib.import_module("copilot_operator")',
    'from importlib import import_module\nx = import_module("copilot_operator")',
    'def f():\n    return __import__("copilot_operator")',
])
def test_the_scan_sees_dynamic_imports_too(source):
    """A statement-only scan reports a clean tree while the violation is there.

    `__import__("copilot_operator")` leaves no `Import` node, so the two names
    this whole change is about could walk straight back in under a spelling the
    guard could not see -- including from inside a function body, which is where
    the four `import operator_trace` calls this change repointed were living.
    """
    assert "copilot_operator" in imported_names(source)


@pytest.mark.parametrize("source", [
    'x = __import__(name)',
    'x = importlib.import_module(module_for(seat))',
    'x = some.other.import_module("copilot_operator")',
])
def test_the_dynamic_scan_does_not_invent_names(source):
    """Negative control.

    A computed name cannot be resolved statically. Reporting one anyway would
    make this scan's failures untrustworthy, and an untrustworthy guard is
    turned off. The third case is a method that merely shares a name with
    `importlib.import_module` -- it is accepted only because the argument is a
    literal, so it is the one false positive worth being honest about: if some
    unrelated `import_module("x")` ever appears here, it will be reported.
    """
    names = imported_names(source)
    assert "name" not in names
    assert "module_for" not in names


def undefined_globals(source: str, filename: str) -> set[str]:
    """Global names a module reads that nothing in it binds.

    `symtable` rather than a hand-rolled AST walk, because the question is
    genuinely about *scope*: a name assigned in one function and read in
    another is undefined, a name assigned at module level and read anywhere is
    fine, and a comprehension has its own scope. The compiler already knows all
    of this and answering it again by hand is how the answer drifts.

    **What this does not do**, said plainly so nobody reads more into a pass
    than is there: it is a binding check, not a definite-assignment one. A name
    bound only on a branch that did not run (`if False: x = 1`), bound after
    its own use (`class C: token = token`, a decorator defined further down),
    or deleted before a read is *bound* as far as this is concerned and will
    not be reported. Ordering analysis is a different and much larger tool. The
    two defects this exists for -- `operator_session` and `catalog_guid` -- were
    both names bound nowhere at all, which is the case it settles completely.

    `exec`, `eval` and attribute access on a bound name are outside it too, for
    the same reason: nothing static can see through them.
    """
    import builtins
    import symtable

    #: Names the interpreter injects into every module namespace. `__path__` is
    #: deliberately absent: only packages get one, and the kernel is flat
    #: modules, so excluding it would hide a real unbound read.
    module_dunders = {
        "__file__", "__name__", "__doc__", "__package__", "__spec__",
        "__loader__", "__builtins__", "__debug__",
    }

    # A star import binds names this analysis cannot enumerate, so anything it
    # said afterwards would be a guess. Rather than guess generously -- which
    # would quietly switch the guard off for that module -- it reports nothing
    # and `test_the_kernel_does_not_use_star_imports` refuses the construct
    # outright, so the escape hatch cannot be reached without failing there.
    for node in ast.walk(ast.parse(source, filename)):
        if isinstance(node, ast.ImportFrom):
            if any(alias.name == "*" for alias in node.names):
                return set()

    top = symtable.symtable(source, filename, "exec")

    def tables(table):
        yield table
        for child in table.get_children():
            yield from tables(child)

    bound = {name for name in top.get_identifiers()
             if top.lookup(name).is_assigned()
             or top.lookup(name).is_imported()
             or top.lookup(name).is_namespace()}
    # A `global x` in a function, and a walrus inside a module-level
    # comprehension, both bind at module scope from a CHILD table. Reading only
    # the top table reports them missing -- a false positive on correct code,
    # which is how a guard earns its deletion.
    for table in tables(top):
        for symbol in table.get_symbols():
            if symbol.is_global() and symbol.is_assigned():
                bound.add(symbol.get_name())
    known = bound | set(dir(builtins)) | module_dunders

    missing: set[str] = set()
    for table in tables(top):
        for symbol in table.get_symbols():
            name = symbol.get_name()
            if name in known:
                continue
            if table.get_type() == "module":
                # At module level every read of an unbound, non-builtin name
                # is undefined; `is_assigned` already excluded the bound ones.
                if symbol.is_referenced() and not symbol.is_assigned():
                    missing.add(name)
            elif symbol.is_global() and symbol.is_referenced():
                missing.add(name)
    return missing


def test_the_kernel_does_not_use_star_imports():
    """`from x import *` is the one construct that blinds the guard above.

    It binds names nothing static can enumerate, so `undefined_globals` reports
    nothing for a module containing one. That escape hatch has to be closed
    somewhere or the guard is optional: any module could switch it off by
    accident. It is closed here rather than by making the analysis guess.
    """
    offenders = []
    for path in kernel_modules():
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom):
                if any(alias.name == "*" for alias in node.names):
                    offenders.append(f"{path.name}: from {node.module} import *")
    assert offenders == [], (
        "star imports blind the undefined-global scan:\n  "
        + "\n  ".join(offenders)
        + "\n\nName what is imported, or the scan silently stops grading this "
        "module."
    )


def test_the_kernel_reads_no_global_it_never_binds():
    """A name used but never bound is a `NameError` waiting for its branch.

    This has now happened twice in the same subsystem, and both times the
    branch was cold enough that no test reached it while `except Exception`
    stood ready to turn it into a plausible answer:

    * `preamble.py` and `supervisor.py` called `operator_session`, which is on
      FORBIDDEN and imported by neither -- caught by
      `test_the_kernel_does_not_use_forbidden_modules_it_never_imported`, but
      only because the name happened to be on that list.
    * `supervisor.py` called `catalog_guid`, which was on no list and defined
      nowhere in the repository. `_loop_work_db` guards it behind
      `if store is None: return`, and nothing injects a store yet, so the
      `NameError` was unreachable until work assignment is switched on -- at
      which point `except Exception` would have logged it and answered `None`,
      which reaches the agent as "you have no assignment".

    The FORBIDDEN scan cannot catch the second: it grades a hand-written list,
    and a list is the thing a new name is absent from. This grades every
    global, so the next one fails here on the day it is written rather than on
    the day the feature is switched on.
    """
    offenders: list[str] = []
    for path in kernel_modules():
        for name in sorted(undefined_globals(
                path.read_text(encoding="utf-8"), str(path))):
            offenders.append(f"{path.name}: {name}")
    assert offenders == [], (
        "the kernel reads globals nothing binds, so every one of these is a "
        "NameError waiting for its branch to be taken:\n  "
        + "\n  ".join(offenders)
        + "\n\nAn `except Exception` around one of these turns it into a "
        "plausible wrong answer rather than a crash, which is how the last "
        "two survived."
    )


def test_the_undefined_global_detector_fires():
    """Positive control, on synthetic source rather than the tree.

    The first spelling is exactly what `supervisor.py` contained: a bare call
    to a name nothing defines, inside a function, under a `try`.
    """
    source = (
        "def _loop_work_db(workdir):\n"
        "    try:\n"
        "        return catalog_guid(workdir).guid\n"
        "    except Exception:\n"
        "        return None\n"
    )
    assert "catalog_guid" in undefined_globals(source, "<synthetic>")


@pytest.mark.parametrize("source", [
    "import catalog\ndef f():\n    return catalog.guid()\n",
    "from paths import catalog_guid\ndef f():\n    return catalog_guid(1)\n",
    "catalog_guid = None\ndef f():\n    return catalog_guid\n",
    "def catalog_guid():\n    pass\ndef f():\n    return catalog_guid()\n",
    "def f(catalog_guid):\n    return catalog_guid\n",
    "def f():\n    catalog_guid = 1\n    return catalog_guid\n",
    "def f():\n    return [x for x in range(3)]\n",
    "def f():\n    return len('x')\n",
    # The forms a reviewer found this reporting as undefined while they run
    # perfectly well. Every one of these is a false positive, and a false
    # positive is worse than a miss: it fires on correct code, and the cheapest
    # way to make it stop is to delete the guard.
    "def f():\n    global seen\n    seen = 1\ndef g():\n    return seen\n",
    "from math import *\ndef f():\n    return sin(1)\n",
    "vals = [y := 2]\ndef f():\n    return y\n",
    "class C:\n    x = 1\ndef f():\n    return C.x\n",
    "import functools\n@functools.cache\ndef f():\n    return 1\n",
    "def f(a: int) -> str:\n    return str(a)\n",
    "try:\n    import json\nexcept ImportError:\n    json = None\ndef f():\n    return json\n",
    "def outer():\n    v = 1\n    def inner():\n        nonlocal v\n        v = 2\n    return inner\n",
])
def test_the_undefined_global_detector_accepts_bound_names(source):
    """Negative controls: a detector that reports everything also 'passes'.

    Every binding form the kernel actually uses is here -- import, from-import,
    assignment, `def`, `class`, parameter, local, comprehension variable,
    builtin, `global`, `nonlocal`, walrus, decorator, annotation and a
    `try/except ImportError` fallback.
    """
    assert undefined_globals(source, "<synthetic>") == set()


def test_a_star_import_makes_the_detector_say_nothing_rather_than_guess():
    """It cannot enumerate what a star import bound, so it declines to answer.

    Reported as a pass here and refused outright by
    `test_the_kernel_does_not_use_star_imports`, which is the pairing that
    keeps "cannot analyse" from quietly becoming "analysed and clean".
    """
    assert undefined_globals(
        "from math import *\ndef f():\n    return definitely_not_bound\n",
        "<synthetic>") == set()


def test_the_star_import_refusal_fires_on_synthetic_source():
    """Positive control for the construct the guard above cannot see through."""
    tree = ast.parse("from math import *\n")
    starred = [n for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)
               and any(a.name == "*" for a in n.names)]
    assert len(starred) == 1


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
