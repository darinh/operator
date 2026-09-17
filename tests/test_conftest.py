"""The suite may not write to the developer's real operator home.

`conftest._no_real_operator_home` is the guard, and this is the check on it.
Every test in this file runs *under* that fixture, so it can assert the state a
test actually sees rather than reconstructing it.

The hazard is specific. `config.py` resolves `OPERATOR_HOME` at import and
derives `RESTART_DIR` and `LOG_FILE` from it there, so a module-level constant
does not follow `COPILOT_OPERATOR_HOME` being set later. A test that relocates
two of the three leaves the third pointing at `~/.operator`, and
`probes.log` reads two of them -- so the test passes and the write lands in the
log the developer reads. Measured before the guard existed: one run of one
passing test appended 1,773 bytes to the real operator log.
"""
from __future__ import annotations

import os
from pathlib import Path

import op
import probes

REAL_HOME = Path.home() / ".operator"


def _under(path, root) -> bool:
    try:
        Path(path).resolve().relative_to(Path(root).resolve())
    except (ValueError, OSError):
        return False
    return True


# ── the three names, as a test sees them ─────────────────────────


def test_the_operator_home_a_test_sees_is_not_the_real_one():
    assert Path(op.OPERATOR_HOME).resolve() != REAL_HOME.resolve()


def test_the_log_file_a_test_sees_is_not_in_the_real_home():
    """The one that leaked. `probes.log` appends here on every kernel message."""
    assert not _under(op.LOG_FILE, REAL_HOME)


def test_the_restart_dir_a_test_sees_is_not_in_the_real_home():
    assert not _under(op.RESTART_DIR, REAL_HOME)


def test_the_three_names_agree_on_one_sandbox():
    """Two of three redirected is the exact shape of the original defect."""
    home = Path(op.OPERATOR_HOME)
    assert _under(op.LOG_FILE, home), "LOG_FILE points outside OPERATOR_HOME"
    assert _under(op.RESTART_DIR, home), "RESTART_DIR points outside OPERATOR_HOME"


def test_the_environment_agrees_with_the_bound_constants():
    """Extensions and both CLIs re-resolve the home from here, not from config."""
    assert os.environ["COPILOT_OPERATOR_HOME"] == str(op.OPERATOR_HOME)


# ── the redirect reaches the modules that hold the names ─────────


def test_every_module_holding_the_log_file_sees_the_sandbox():
    """`from config import LOG_FILE` copies the value; the shim must reach them.

    Asserted over `holders_of` rather than a hand-written list, so a new kernel
    module that binds the name is covered the day it appears.
    """
    holders = op.holders_of("LOG_FILE")
    assert holders, "no module binds LOG_FILE; this test is no longer meaningful"
    for module in holders:
        assert getattr(module, "LOG_FILE") == op.LOG_FILE, (
            f"{module.__name__} still holds its own LOG_FILE")


def test_every_module_holding_the_operator_home_sees_the_sandbox():
    holders = op.holders_of("OPERATOR_HOME")
    assert holders, "no module binds OPERATOR_HOME"
    for module in holders:
        assert getattr(module, "OPERATOR_HOME") == op.OPERATOR_HOME, (
            f"{module.__name__} still holds its own OPERATOR_HOME")


# ── the end-to-end property, through the real writer ─────────────


def test_a_kernel_log_line_lands_in_the_sandbox_and_not_the_real_log():
    """The actual leak path, driven rather than reasoned about.

    `probes.log` is what wrote those 1,773 bytes. It mkdirs `OPERATOR_HOME` and
    appends to `LOG_FILE`, which is why redirecting only one of them was worse
    than redirecting neither: the sandbox was created and the real log was
    written.
    """
    real_log = REAL_HOME / "operator.log"
    before = real_log.stat().st_size if real_log.exists() else None

    probes.log("a line written by test_conftest")

    assert Path(op.LOG_FILE).exists(), "the sandbox log was not created"
    assert "a line written by test_conftest" in Path(op.LOG_FILE).read_text(
        encoding="utf-8")

    after = real_log.stat().st_size if real_log.exists() else None
    assert after == before, (
        f"the real operator log changed during a test: {before} -> {after}")


def test_the_sandbox_is_writable_so_the_guard_does_not_just_break_logging():
    """A guard that pointed at an unwritable path would look identical here."""
    probes.log("first")
    probes.log("second")
    body = Path(op.LOG_FILE).read_text(encoding="utf-8")
    assert "first" in body and "second" in body


def test_each_test_gets_its_own_sandbox(tmp_path):
    """State must not carry between tests; the previous test wrote to its own.

    The two lines above cannot be here, because this is a different sandbox.
    """
    assert "a line written by test_conftest" not in (
        Path(op.LOG_FILE).read_text(encoding="utf-8")
        if Path(op.LOG_FILE).exists() else "")
