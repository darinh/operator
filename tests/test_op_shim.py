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


def test_the_stray_module_detector_would_have_fired_on_the_one_that_slipped():
    """Positive control, on the exact module that got through.

    Scored against the tree alone this file starts passing the moment the tree
    is clean, which is when a detector's silence stops being evidence.
    """
    import trace as stdlib_trace

    origin = Path(stdlib_trace.__file__).resolve()
    assert KERNEL not in origin.parents, (
        "the standard library's `trace` now appears to live in the kernel; "
        "this control no longer grades anything"
    )
    assert not (KERNEL / "trace.py").exists(), (
        "a kernel module named `trace` would shadow the standard library -- "
        "see test_no_module_name_collisions.py"
    )


@pytest.mark.parametrize("alias", sorted(op._ALIASES))
def test_every_alias_resolves_to_the_kernel_module_it_claims(alias):
    """An alias is a rename recorded in one place, so it must be the right one.

    `operator_trace` was mapped to `"trace"`, which is not a kernel module at
    all. The renamed behaviour lives in `evidence.py`.
    """
    target = op._ALIASES[alias]
    assert target in op._MODULE_NAMES, (
        f"alias {alias!r} points at {target!r}, which the shim does not bind"
    )
    assert getattr(op, alias) is getattr(op, target)


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


# ── the forwarding claim ────────────────────────────────────────
def test_a_write_reaches_every_module_binding_the_name(monkeypatch):
    """The docstring's first claim, on a name several modules hold.

    `RESTART_DIR` is the example the shim's own docstring uses: `config`
    defines it and `instance` does `from config import RESTART_DIR`, so a write
    that reached only `config` would leave the reader pointed at the real
    `~/.operator` while the test believed it had been relocated.
    """
    holders = op.holders_of("RESTART_DIR")
    assert len(holders) >= 2, (
        "RESTART_DIR is held by fewer than two modules, so this test no longer "
        "grades forwarding -- pick a name that is imported across modules"
    )
    sentinel = Path("/sentinel/restart")
    monkeypatch.setattr(op, "RESTART_DIR", sentinel)
    unreached = [m.__name__ for m in holders
                 if getattr(m, "RESTART_DIR", None) != sentinel]
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
