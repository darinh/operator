"""The `op` shim, graded on the two claims its docstring makes.

`tests/op.py` says "`test_op_shim.py` asserts that it does". No such file
existed. That is a small instance of the failure this repository keeps finding:
a guarantee written in prose, believed by everyone reading it, and checked by
nothing -- and in this case both claims turned out to be worth checking, because
one of them was false.

The shim presents the kernel's modules as the single namespace the migrated
tests were written against. Two properties make it safe to do that:

1. **Writes forward to every module binding the name.** Without it,
   `monkeypatch.setattr(op, "RESTART_DIR", tmp)` would succeed and change
   nothing, and a test relocating its state would run against the developer's
   real `~/.operator` while reporting a pass.
2. **Every name it binds is a kernel module.** This one was not true.
   `_MODULE_NAMES` listed `"trace"`, there is no `operator_kernel/trace.py`, and
   so the standard library's tracing module was bound into the kernel namespace
   -- taking `op.operator_trace`, `op.main` and every other public name in it
   along the way.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

import op

REPO = Path(__file__).resolve().parent.parent
KERNEL = REPO / "operator_kernel"


def _bound_modules():
    """Every module object the shim exposes under one of its declared names."""
    return {name: getattr(op, name) for name in op._MODULE_NAMES}


def test_the_shim_binds_the_modules_it_declares():
    """Without this, an empty binding satisfies every assertion below."""
    assert len(op._MODULE_NAMES) >= 10
    assert len(_bound_modules()) == len(op._MODULE_NAMES)


def test_every_module_the_shim_binds_lives_in_the_kernel():
    """The claim that was false.

    A name here that is not a kernel module does not fail -- it imports
    something else and lends that module the kernel's spelling. `"trace"` did
    this for the life of the extraction, and the symptom was four tests asking
    Python's coverage tracer for `trace_path`.
    """
    strays = []
    for name, module in sorted(_bound_modules().items()):
        origin = getattr(module, "__file__", None)
        if origin is None or KERNEL not in Path(origin).resolve().parents:
            strays.append(f"{name} -> {origin}")
    assert strays == [], (
        "the shim binds modules from outside the kernel:\n  "
        + "\n  ".join(strays)
        + "\n\nTheir contents enter the namespace under kernel spellings, and "
        "a test patching one of those names patches somebody else's module."
    )


def test_the_stray_module_detector_refuses_the_module_that_slipped():
    """Positive control, feeding the real predicate the module that got through.

    An earlier version of this test asserted only that the standard library's
    `trace` does not live under `operator_kernel/` and that
    `operator_kernel/trace.py` does not exist. Both were true BEFORE the fix and
    stay true if somebody replaces the detector with `strays = []`. It graded
    the filesystem, not the guard. `is_kernel_module` was extracted from `_bind`
    so the control can hand it the exact module that slipped and watch it be
    refused.
    """
    import trace as stdlib_trace

    assert op.is_kernel_module(stdlib_trace) is False, (
        "the guard accepts the standard library's `trace`, which is what it "
        "silently bound into the kernel namespace before this fix"
    )
    assert not (KERNEL / "trace.py").exists(), (
        "a kernel module named `trace` would shadow the standard library -- "
        "see test_no_module_name_collisions.py -- and would make this control "
        "grade nothing"
    )


def test_the_stray_module_detector_accepts_a_real_kernel_module():
    """Negative control: a predicate that refuses everything also 'passes'."""
    assert op.is_kernel_module(op.config) is True


def test_the_stray_module_detector_refuses_a_module_with_no_file():
    """Builtins and namespace packages have no `__file__`; neither is a kernel."""
    import sys as stdlib_sys

    assert getattr(stdlib_sys, "__file__", None) is None
    assert op.is_kernel_module(stdlib_sys) is False


@pytest.mark.parametrize("alias", sorted(op._ALIASES))
def test_every_alias_resolves_to_a_module_the_shim_binds(alias):
    """A consistency check, and *only* that -- which is worth saying plainly.

    This would have passed against the defect. Before the fix,
    `_MODULE_NAMES` contained `"trace"` and `_ALIASES["operator_trace"]` was
    `"trace"`, so membership held and both names resolved to the identical
    (wrong) module, satisfying every assertion here. The tests that would have
    caught it are `test_every_module_the_shim_binds_lives_in_the_kernel` and
    `test_the_renamed_trace_module_has_what_its_callers_ask_for`. An earlier
    version of this docstring claimed the credit; it was wrong, and a wrong
    claim about which test catches which defect is how the wrong one gets
    deleted later as redundant.
    """
    target = op._ALIASES[alias]
    assert target in op._MODULE_NAMES, (
        f"alias {alias!r} points at {target!r}, which the shim does not bind"
    )
    assert getattr(op, alias) is getattr(op, target)


def test_the_alias_that_was_wrong_is_pinned_to_the_module_that_is_right():
    """The mapping itself, pinned by name.

    The generic checks above are all satisfiable by a self-consistent wrong
    answer, so the one mapping known to have been wrong is written down.
    """
    assert op._ALIASES["operator_trace"] == "evidence"
    assert "trace" not in op._MODULE_NAMES, (
        "`trace` is back in the bind list; there is no operator_kernel/trace.py "
        "and it resolves to the standard library's tracing module"
    )


def test_the_renamed_trace_module_has_what_its_callers_ask_for():
    """The alias being *a* kernel module is not enough; it must be the right one.

    Both halves are needed. `operator_trace -> trace` failed the test above,
    but an alias pointing at some other real kernel module would pass it while
    still being wrong, and the tests using it would fail somewhere far away.
    """
    for attribute in ("trace_path", "ancestry"):
        assert hasattr(op.operator_trace, attribute), (
            f"`operator_trace` resolves to "
            f"{getattr(op.operator_trace, '__name__', '?')}, which has no "
            f"{attribute!r}; the tests using this alias want the module that does"
        )


def _kernel_modules_binding(name: str) -> set[str]:
    """Kernel modules holding a module-level `name`, read off the source.

    Derived from disk and NOT from `op.holders_of`, which is the thing under
    test. Scoring the forwarding map against itself makes a module the shim
    forgot invisible twice: absent from the expected set and absent from the
    write, so the assertion passes by agreeing with the defect.
    """
    holders: set[str] = set()
    for path in sorted(KERNEL.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:
            if isinstance(node, ast.ImportFrom):
                if any(alias.name == name for alias in node.names):
                    holders.add(path.stem)
            elif isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id == name:
                        holders.add(path.stem)
    return holders


def test_the_forwarding_map_matches_what_the_source_says(monkeypatch):
    """`holders_of` must agree with an independent reading of the kernel."""
    for name in ("RESTART_DIR", "MUX", "OPERATOR_HOME"):
        from_source = _kernel_modules_binding(name)
        from_shim = {module.__name__ for module in op.holders_of(name)}
        assert from_source, f"no kernel module binds {name}; pick another name"
        assert from_source <= from_shim, (
            f"`holders_of({name!r})` misses "
            f"{sorted(from_source - from_shim)}. A write through `op` would "
            f"skip them, which is exactly how the multiplexer substitution "
            f"managed to look applied."
        )


# ── the forwarding claim ────────────────────────────────────────
def test_a_write_reaches_every_module_binding_the_name(monkeypatch):
    """The docstring's first claim, on a name several modules hold.

    `RESTART_DIR` is the example the shim's own docstring uses: `config`
    defines it and `instance` does `from config import RESTART_DIR`, so a write
    that reached only `config` would leave the reader pointed at the real
    `~/.operator` while the test believed it had been relocated.

    The expected set comes from the source, not from `holders_of` -- see
    `_kernel_modules_binding`.
    """
    expected = _kernel_modules_binding("RESTART_DIR")
    assert len(expected) >= 2, (
        "RESTART_DIR is held by fewer than two modules, so this test no longer "
        "grades forwarding -- pick a name that is imported across modules"
    )
    sentinel = Path("/sentinel/restart")
    monkeypatch.setattr(op, "RESTART_DIR", sentinel)
    unreached = sorted(
        name for name in expected
        if getattr(__import__(name), "RESTART_DIR", None) != sentinel
    )
    assert unreached == [], f"the write did not reach: {unreached}"


def test_the_write_is_undone_when_the_patch_is(monkeypatch):
    """A forwarded write that is not forwarded back leaks into the next test.

    Checked through monkeypatch's own undo rather than by restoring by hand,
    because that is how every caller uses it.
    """
    holders = op.holders_of("RESTART_DIR")
    before = {m.__name__: getattr(m, "RESTART_DIR", None) for m in holders}

    with monkeypatch.context() as patched:
        patched.setattr(op, "RESTART_DIR", Path("/sentinel/restart"))

    after = {m.__name__: getattr(m, "RESTART_DIR", None) for m in holders}
    assert after == before, "a forwarded write outlived the patch that made it"


def test_reading_back_a_forwarded_write_agrees_with_the_modules():
    """The namespace must not disagree with the modules it forwards to.

    A shim that updated the owners but not its own `__dict__` would answer the
    old value to `op.X` while the kernel used the new one -- the same class of
    split-brain the multiplexer substitution produced.
    """
    holders = op.holders_of("RESTART_DIR")
    sentinel = Path("/sentinel/readback")
    original = op.RESTART_DIR
    try:
        op.RESTART_DIR = sentinel
        assert op.RESTART_DIR == sentinel
        for module in holders:
            assert getattr(module, "RESTART_DIR", None) == sentinel
    finally:
        op.RESTART_DIR = original


def test_a_name_no_module_binds_is_not_claimed_to_have_holders():
    """Negative control: `holders_of` must not answer confidently about nothing."""
    assert op.holders_of("definitely_not_a_kernel_name_xyzzy") == ()
