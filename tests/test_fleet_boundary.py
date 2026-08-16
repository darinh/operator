"""The fleet package's boundary, written the day the package appeared.

`operator_fleet/` exists because the kernel hit its budget with nothing left to
add, and the honest response to that is a cut rather than a bigger constant.
But a cut into an unguarded directory is not a cut -- it is a place to put
things where nothing counts them, and every rule the kernel is held to would
then be one `git mv` away from not applying. The budget's own comment says
"cut before you raise"; this file is what stops that from meaning "move it
somewhere the numbers do not look".

So the same four questions are asked here, with the answers this package's
different job deserves:

* **What may it import?** The standard library, itself, and the kernel. Not the
  reverse -- the arrow between the two packages points one way, and
  `test_the_kernel_imports_nothing_it_is_defined_as_not_being` holds that end.
* **How big may it get?** Its own ceilings, low enough that the next thing to
  land here is a decision. They are not the kernel's numbers: this is a smaller
  package with a smaller job, and copying 4,100 across would have made the
  budget vacuous on the day it was written.
* **May a module here shadow something already importable?** No, and this is
  the sharper question of the four, because `operator_fleet/` went onto
  `pythonpath` beside the kernel. `snapshot` is a far more ordinary name than
  `supervisor_records`, and the collision failure in
  `test_no_module_name_collisions.py` cost seventy green tests grading the
  wrong repository.
* **May it import a third-party package?** Only what the kernel may, which is
  nothing. A fleet board that needs a web framework is a real possibility and a
  real decision; this makes somebody write it down.

The scanners are the kernel boundary's own, imported rather than copied. A
second implementation of `code_lines` would drift from the first, and the
divergence would appear as one package being charged differently from the
other for the same source.
"""
from __future__ import annotations

import ast
import re

import pytest

from test_kernel_boundary import (ALLOWED_THIRD_PARTY, FLEET, FORBIDDEN,
                                  KERNEL, MAX_MODULE_LINES, REPO, code_lines,
                                  fleet_modules, imported_names,
                                  kernel_modules, undefined_globals)

#: The fleet's complexity budget, in code lines. Set at the measured 60 plus
#: room for the board and the fleet host (`on_fact`, `on_tick`, `propose_work`)
#: that `docs/plan.md` says belong out here -- generous for what exists,
#: binding well before this becomes a second god module. The kernel's 4,100 is
#: not the reference point: this package supervises nothing.
MAX_FLEET_CODE_LINES = 600

#: Total lines, for navigability, on the same 1:1.5 ratio the kernel's two
#: ceilings sit at. Present because the kernel's total ceiling was once deleted
#: and nothing noticed for a while.
MAX_FLEET_TOTAL_LINES = 1200


def test_there_are_fleet_modules_to_check():
    """Every scan below reports a clean tree over an empty directory."""
    assert len(fleet_modules()) >= 1
    assert (FLEET / "snapshot.py").exists(), (
        "`snapshot.py` is the extraction this package was created by. If it "
        "moved again, this file's premise needs rewriting rather than deleting"
    )


def test_the_fleet_imports_nothing_it_is_defined_as_not_being():
    """Standard library, itself, and the kernel. Nothing else without a decision."""
    import sys

    stdlib = sys.stdlib_module_names
    kernel = {p.stem for p in kernel_modules()}
    ours = {p.stem for p in fleet_modules()}
    offenders: list[str] = []
    for path in fleet_modules():
        for name in imported_names(path.read_text(encoding="utf-8")):
            if name in FORBIDDEN:
                offenders.append(f"{path.name}: {name} (forbidden)")
            elif (name not in stdlib and name not in ours
                    and name not in kernel
                    and name not in ALLOWED_THIRD_PARTY):
                offenders.append(f"{path.name}: {name} (undeclared)")
    assert offenders == [], (
        "the fleet package reached outside what it is allowed:\n  "
        + "\n  ".join(offenders)
        + "\n\nThis package may read the kernel and the standard library. A "
        "third-party dependency here is a dependency of the whole repository, "
        "and `ALLOWED_THIRD_PARTY` is where that gets argued."
    )


def test_the_fleet_stays_under_its_budget():
    total = sum(code_lines(p.read_text(encoding="utf-8"))
                for p in fleet_modules())
    assert total <= MAX_FLEET_CODE_LINES, (
        f"operator_fleet is {total} code lines, budget "
        f"{MAX_FLEET_CODE_LINES}. This package was created to receive a cut "
        f"from the kernel, not to be the place cuts stop being counted."
    )


def test_the_fleet_stays_under_its_total_ceiling():
    total = sum(len(p.read_text(encoding="utf-8").splitlines())
                for p in fleet_modules())
    assert total <= MAX_FLEET_TOTAL_LINES, (
        f"operator_fleet is {total} total lines, ceiling "
        f"{MAX_FLEET_TOTAL_LINES}."
    )


def test_no_fleet_module_exceeds_the_line_ceiling():
    """The per-file ceiling, shared with the kernel because it is about scrolling."""
    oversize = [
        f"{p.name}: {len(p.read_text(encoding='utf-8').splitlines())} lines"
        for p in fleet_modules()
        if len(p.read_text(encoding="utf-8").splitlines()) > MAX_MODULE_LINES
    ]
    assert oversize == [], (
        f"over the {MAX_MODULE_LINES}-line module ceiling:\n  "
        + "\n  ".join(oversize)
    )


def test_the_fleet_does_not_use_star_imports():
    """`from x import *` makes the ghost-name check below unable to answer.

    Carried over from the kernel rather than assumed unnecessary here. It is a
    two-line check, and the guard it protects is one that reports a clean tree
    when it cannot see.
    """
    offenders = []
    for path in fleet_modules():
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom):
                if any(alias.name == "*" for alias in node.names):
                    offenders.append(f"{path.name}: from {node.module} import *")
    assert offenders == [], "star imports in the fleet package:\n  " + "\n  ".join(offenders)


def test_the_fleet_reads_no_global_it_never_binds():
    """The ghost-name guard, applied to the package a file was moved into.

    The kernel has this because it was reading three globals nothing bound,
    one of which would have answered "you have no assignment" the day work
    assignment was switched on. A module carried across a package boundary is
    exactly when a name stops resolving -- `snapshot.py` lost five imports on
    the way here -- so the check has to travel with it rather than stay
    pointed at the directory it left.
    """
    ghosts = []
    for path in fleet_modules():
        for name in sorted(undefined_globals(path.read_text(encoding="utf-8"),
                                             path.name)):
            ghosts.append(f"{path.name}: {name}")
    assert ghosts == [], (
        "the fleet package reads names nothing binds:\n  " + "\n  ".join(ghosts)
    )


def test_the_ghost_name_guard_fires_on_the_fleet_scanner(tmp_path):
    """Positive control: the imported detector, run the way this file runs it.

    `test_kernel_boundary.py` controls `undefined_globals` itself. This one
    controls the loop above -- that it passes the source and reports what came
    back -- because a scan that reads the wrong attribute reports every file
    clean and looks identical to a clean package.
    """
    source = "def f():\n    return no_such_name\n"
    assert "no_such_name" in undefined_globals(source, "fake.py")
    assert undefined_globals("x = 1\n\n\ndef f():\n    return x\n", "fake.py") == set()


def test_no_fleet_module_shadows_a_standard_library_module():
    """`snapshot` is an ordinary word, and this directory is on `pythonpath`.

    The kernel has the same rule for the same reason. It is repeated rather
    than shared because the two packages are scanned from different roots and
    a helper taking a root would have had exactly one caller until now.
    """
    import sys

    names = {p.stem for p in fleet_modules() if p.stem != "__init__"}
    shadows = sorted(names & set(sys.stdlib_module_names))
    assert shadows == [], (
        f"fleet modules shadow the standard library: {shadows}. A module named "
        f"`code` or `types` is imported by something that wanted the real one."
    )


def test_no_fleet_module_collides_with_a_kernel_module():
    """Both directories are on `pythonpath`, so a shared stem is a coin toss.

    Which one `import snapshot` resolves to would depend on the order
    `pyproject`'s `pythonpath` happens to list, and a guard that passes under
    one invocation and fails under another is not a guard -- the reasoning
    `test_no_module_name_collisions.py` already records, applied to the
    collision this package's existence newly makes possible.
    """
    kernel = {p.stem for p in kernel_modules()}
    ours = {p.stem for p in fleet_modules()}
    clashes = sorted((kernel & ours) - {"__init__"})
    assert clashes == [], (
        f"these names exist in both packages: {clashes}. Both directories are "
        f"on `pythonpath`, so the winner depends on their order there."
    )


# ── controls ────────────────────────────────────────────────────

def test_the_fleet_scanners_are_reading_the_fleet():
    """Positive control: the scanners must not be pointed at an empty set.

    Every assertion above is satisfied by `fleet_modules()` returning nothing,
    which is precisely how a guard reports a clean tree while checking none.
    """
    assert FLEET.is_dir()
    assert FLEET != KERNEL
    measured = sum(code_lines(p.read_text(encoding="utf-8"))
                   for p in fleet_modules())
    assert measured > 0, "the budget above is counting an empty package"


def test_the_fleet_budget_would_fire():
    """Negative control on the metric, not on the tree.

    The budget assertions pass today by a wide margin, so nothing in this file
    demonstrates that they *can* fail. This feeds the real counter a payload
    over the ceiling and watches it exceed -- the same reason every detector in
    `test_kernel_boundary.py` is violated once on purpose.
    """
    over = "x = 1\n" * (MAX_FLEET_CODE_LINES + 1)
    assert code_lines(over) > MAX_FLEET_CODE_LINES


def _declared_list(text: str, key: str) -> list[str]:
    """The string entries of a single-line TOML array assigned to ``key``.

    Hand-parsed rather than read with `tomllib`, and that is a version
    decision rather than a preference: `pyproject.toml` says
    `requires-python = ">=3.10"`, `tomllib` arrived in 3.11, and a test that
    imports it would fail on the oldest interpreter this repository claims to
    support. A `try: import tomllib / except: skip` is worse -- a guard that
    quietly stops running on the platform it is least tested on is the shape
    of the failures recorded throughout this suite.

    Membership is what the caller asks about, never order or spacing. An
    earlier version string-matched the whole line, so `["operator_fleet",
    "operator_kernel"]` -- identical in meaning -- failed it, which is a test
    pinning the formatting it happened to be written against.
    """
    match = re.search(rf"^\s*{re.escape(key)}\s*=\s*\[(.*?)\]", text,
                      re.MULTILINE | re.DOTALL)
    if match is None:
        return []
    return re.findall(r'"([^"]*)"', match.group(1))


def test_the_fleet_package_is_declared_where_it_has_to_be():
    """A source package nothing installs or imports is a directory.

    `pyproject.toml` has to name it twice -- once so the suite can import it,
    once so a build includes it -- and the failure of the second is silent
    until somebody installs the distribution and finds the module missing.
    """
    text = (REPO / "pyproject.toml").read_text(encoding="utf-8")
    packages = _declared_list(text, "packages")
    pythonpath = _declared_list(text, "pythonpath")
    assert "operator_kernel" in packages and "operator_fleet" in packages, (
        f"`packages` is {packages}; a package missing here is absent from an "
        f"installed distribution while the suite, which reads the checkout, "
        f"goes on passing"
    )
    assert "operator_kernel" in pythonpath and "operator_fleet" in pythonpath, (
        f"`pythonpath` is {pythonpath}; a package missing here is not "
        f"importable by the suite at all"
    )


def test_the_declaration_reader_reads_something():
    """Positive control: `_declared_list` returning `[]` passes nothing above.

    A regex that stops matching -- a reformat to a multi-line array, a rename
    of the table -- makes both assertions above `"x" in []`, which fails
    loudly. This pins the opposite direction: that the reader finds the real
    entries, so the test is grading the file rather than the regex.
    """
    text = (REPO / "pyproject.toml").read_text(encoding="utf-8")
    assert _declared_list(text, "packages"), (
        "the reader found no `packages` array; it has stopped parsing the file"
    )
    assert _declared_list(text, "testpaths") == ["tests"]
    assert _declared_list(text, "not_a_key_in_this_file") == []


@pytest.mark.parametrize("array, expected", [
    pytest.param('["a", "b"]', ["a", "b"], id="spaced"),
    pytest.param('["b","a"]', ["b", "a"], id="tight and reordered"),
    pytest.param('[ "a" ]', ["a"], id="padded"),
    pytest.param('[]', [], id="empty"),
])
def test_the_declaration_reader_ignores_formatting(array, expected):
    """The reason it is not a string match: these all mean the same thing."""
    assert _declared_list(f"key = {array}\n", "key") == expected
