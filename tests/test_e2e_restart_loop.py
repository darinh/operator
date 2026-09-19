"""The harness's own helpers, and the two properties that make it safe to run.

`e2e_restart_loop.py` drives real processes against a real multiplexer, so it
cannot be part of the pytest suite -- `conftest` substitutes the multiplexer
precisely so that no test can create a live session. These tests cover the
parts that can be checked without doing any of that, and they concentrate on
the two ways this harness could do damage rather than on its arithmetic.

The first is the home. `config.py` resolves `OPERATOR_HOME` at import, so a
harness that imported the kernel at module scope would bind the developer's
real home before `main` ever set the variable, and then drive it.
`operator_cli/recover.py` shipped exactly that bug and was caught listing
eleven real seats. The import order here is asserted rather than trusted.

The second is the session name. The harness kills sessions during cleanup, so
a fixed name would mean two concurrent runs killing each other's -- or,
worse, a developer's own session that happened to share it.
"""
from __future__ import annotations

import ast
import importlib.util
import os
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
HARNESS = REPO / "e2e_restart_loop.py"


def _load():
    spec = importlib.util.spec_from_file_location("e2e_restart_loop_under_test",
                                                  HARNESS)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


harness = _load()


# ── it must not bind the real operator home on import ────────────


def test_the_harness_imports_no_kernel_module_at_module_scope():
    """The bug `operator_cli/recover.py` shipped, in the one file that drives
    real processes.

    `config.py` resolves `OPERATOR_HOME` at import and derives `RESTART_DIR`
    from it there. A kernel import at module scope would therefore bind the
    developer's real home before `main` sets `COPILOT_OPERATOR_HOME` -- and
    this harness spawns supervisors and kills sessions.

    Checked over the AST at module level, because that is exactly the
    distinction that matters: the same imports inside `main`, after the
    variable is set, are correct and are what the file does.
    """
    tree = ast.parse(HARNESS.read_text(encoding="utf-8"))
    kernel_names = {p.stem for p in (REPO / "operator_kernel").glob("*.py")}
    offenders = []
    for node in tree.body:  # module level only, deliberately
        if isinstance(node, ast.Import):
            offenders += [a.name for a in node.names
                          if a.name.split(".")[0] in kernel_names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            if node.module.split(".")[0] in kernel_names:
                offenders.append(node.module)
    assert offenders == [], (
        f"{sorted(offenders)} are imported before COPILOT_OPERATOR_HOME is "
        f"set, so the kernel binds the developer's real home and this harness "
        f"drives it")


def test_the_home_is_set_before_the_kernel_is_imported():
    """The positive half: `main` does set it, and sets it first."""
    source = HARNESS.read_text(encoding="utf-8")
    set_at = source.index('os.environ["COPILOT_OPERATOR_HOME"]')
    import_at = source.index("from operator_cli.fleet import _bootstrap")
    assert set_at < import_at, (
        "the kernel is imported before the operator home is exported")


# ── it must not collide with anybody else's session ──────────────


def test_the_session_name_is_unique_to_this_process():
    """Cleanup kills the session by name, so a fixed one is a loaded gun.

    Two concurrent runs would kill each other's, and a developer with a
    session of the same name would lose it to a test harness.
    """
    assert str(os.getpid()) in harness.NAME


def test_the_session_name_is_a_usable_identifier():
    """It becomes a multiplexer session name and a set of file names."""
    assert harness.NAME.isalnum(), harness.NAME


# ── read_pid: the file it reads carries stamps ───────────────────


def test_a_stamped_pid_file_reads_as_its_pid(tmp_path):
    """`_loop_pid_stamp` writes the pid first and `key=value` lines after it,
    so reading the whole file as one integer reports every supervisor as
    never having come up."""
    path = tmp_path / "x.loop.pid"
    path.write_text("4242\npid_start=win:123\nboot=abc\n", encoding="utf-8")
    assert harness.read_pid(path) == 4242


def test_a_bare_pid_file_still_reads(tmp_path):
    path = tmp_path / "x.loop.pid"
    path.write_text("4242\n", encoding="utf-8")
    assert harness.read_pid(path) == 4242


def test_an_absent_pid_file_is_not_an_error(tmp_path):
    """The harness polls this while waiting for a supervisor to come up, so
    absence is the normal case rather than a failure."""
    assert harness.read_pid(tmp_path / "nothing.pid") is None


@pytest.mark.parametrize("payload, why", [
    pytest.param(b"", "empty file", id="empty"),
    pytest.param(b"\xff\xfe4242\n", "invalid UTF-8 raises ValueError, not OSError",
                 id="damaged encoding"),
    pytest.param(b"not-a-pid\n", "unparseable first line", id="not a number"),
])
def test_an_unreadable_pid_file_answers_none(tmp_path, payload, why):
    path = tmp_path / "x.loop.pid"
    path.write_bytes(payload)
    assert harness.read_pid(path) is None, why


# ── waiting ──────────────────────────────────────────────────────


def test_waiting_returns_the_first_truthy_answer():
    answers = iter([None, None, "up"])
    assert harness.wait_for(lambda: next(answers), timeout=5, interval=0) == "up"


def test_waiting_gives_up_rather_than_hanging():
    """Every check in the harness is reported; a wait that never returned
    would replace a named failure with a hung run."""
    assert harness.wait_for(lambda: None, timeout=0.2, interval=0.05) is None


def test_a_changed_pid_is_only_reported_once_it_differs(tmp_path):
    """The restart is proven by the pid *changing*, so the old one must not
    satisfy the wait."""
    path = tmp_path / "x.loop.pid"
    path.write_text("100\n", encoding="utf-8")
    assert harness._changed_pid(path, 100) is None
    path.write_text("200\n", encoding="utf-8")
    assert harness._changed_pid(path, 100) == 200


# ── liveness ─────────────────────────────────────────────────────


def test_this_process_reads_as_alive():
    assert harness.pid_alive(os.getpid()) is True


def test_nothing_reads_as_not_alive():
    assert harness.pid_alive(None) is False
    assert harness.pid_alive(0) is False


# ── the checklist ────────────────────────────────────────────────


def test_a_failed_check_is_recorded_and_reported():
    """The harness exits non-zero on any failure, decided from this list."""
    before = list(harness.failures)
    try:
        harness.failures.clear()
        assert harness.check("a passing thing", True) is True
        assert harness.check("a failing thing", False) is False
        assert harness.failures == ["a failing thing"]
    finally:
        harness.failures[:] = before
