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

from pathlib import Path

import pytest

from test_kernel_boundary import (ALLOWED_THIRD_PARTY, FLEET, FORBIDDEN,
                                  KERNEL, MAX_MODULE_LINES, REPO, code_lines,
                                  fleet_modules, imported_names,
                                  kernel_modules)

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


def test_the_fleet_package_is_declared_where_it_has_to_be():
    """A source package nothing installs or imports is a directory.

    `pyproject.toml` has to name it twice -- once so the suite can import it,
    once so a build includes it -- and the failure of the second is silent
    until somebody installs the distribution and finds the module missing.
    """
    text = (REPO / "pyproject.toml").read_text(encoding="utf-8")
    assert '"operator_fleet"' in text
    assert 'packages = ["operator_kernel", "operator_fleet"]' in text
    assert 'pythonpath = ["operator_kernel", "operator_fleet", "tests"]' in text
