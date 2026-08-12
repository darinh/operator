"""No kernel module may share a name with something already importable.

This exists because the same failure happened three times in one afternoon, and
the third time it produced a green test suite that was testing the wrong code.

`copilot-tools` — the system this kernel is extracted from — is installed
editable on this machine. It exposes top-level modules called `operator_runner`,
`operator_mux`, `operator_console` and `copilot_tools_version`. The kernel began
life with files of exactly those names, so `import operator_runner` under pytest
resolved to the **old** repository's file. Seventy-odd tests passed against the
system being replaced while reporting that the extraction had preserved
behaviour. Nothing about the output distinguished that from success.

An editable install resolves through a `sys.meta_path` finder rather than a
`sys.path` entry, so it cannot be filtered out by inspecting `sys.path` — an
earlier attempt to do exactly that reported `removed -1 entries` and changed
nothing. The only reliable defence is not to collide in the first place.

The check is on *names*, not on resolution order, deliberately. Resolution order
depends on how the suite was invoked, and a guard that passes under `pytest` and
fails under `pytest tests/` is not a guard.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
KERNEL = REPO / "operator_kernel"


def kernel_module_names() -> list[str]:
    return sorted(p.stem for p in KERNEL.glob("*.py") if p.stem != "__init__")


def resolves_outside_kernel(name: str) -> str | None:
    """Where ``name`` would import from if the kernel were not on the path."""
    kernel = str(KERNEL)
    saved = sys.path[:]
    saved_mod = sys.modules.pop(name, None)
    try:
        sys.path = [p for p in sys.path if str(Path(p or ".").resolve()) != kernel]
        spec = importlib.util.find_spec(name)
        return getattr(spec, "origin", None) if spec else None
    except (ImportError, ValueError):
        return None
    finally:
        sys.path = saved
        if saved_mod is not None:
            sys.modules[name] = saved_mod


def test_there_are_modules_to_check():
    assert len(kernel_module_names()) >= 10


def test_no_kernel_module_name_is_importable_from_anywhere_else():
    clashes = []
    for name in kernel_module_names():
        origin = resolves_outside_kernel(name)
        if origin and str(KERNEL) not in str(origin):
            clashes.append(f"{name} -> {origin}")
    assert clashes == [], (
        "these kernel module names also exist outside the kernel:\n  "
        + "\n  ".join(clashes)
        + "\n\nWhichever one wins depends on how the suite was invoked. Rename "
        "the kernel module. This exact collision made 70 tests pass against the "
        "system being replaced."
    )


def test_no_kernel_module_shadows_a_standard_library_module():
    std = sys.stdlib_module_names
    shadows = sorted(set(kernel_module_names()) & set(std))
    assert shadows == [], (
        f"kernel modules shadow the standard library: {shadows}. A module named "
        f"`evidence` or `types` is imported by something that wanted the real one."
    )


# ── controls ────────────────────────────────────────────────────
def test_the_probe_finds_a_module_that_really_is_elsewhere():
    """Positive control: a name that certainly resolves outside the kernel."""
    assert resolves_outside_kernel("json") is not None


def test_the_probe_answers_none_for_a_name_nothing_defines():
    assert resolves_outside_kernel("definitely_not_a_module_xyzzy") is None


@pytest.mark.parametrize("name", ["operator_runner", "operator_mux",
                                  "operator_console", "copilot_tools_version"])
def test_the_names_that_caused_this_are_not_reused(name):
    """The four that collided are named here so a re-introduction is loud.

    A future extraction that ports one of these under its original name would
    otherwise reproduce the exact failure, and the generic check above only
    catches it while the old system remains installed.
    """
    assert name not in kernel_module_names(), (
        f"{name!r} is back. It collides with the installed copilot-tools "
        f"distribution and silently wins or loses depending on invocation."
    )
