"""Tests for the verify-operator harness itself.

A harness bug does not produce a failed verification, it produces a *false* one:
a run that writes to the wrong home, or an `evidence` call that quietly misses
the file the proof depended on, is worse than no verification at all. So the
checkable logic is tested here, separately from the live pass.

These do not spawn the operator CLIs -- driving the real commands is what the
skill's live pass is for. What is covered here is everything the harness decides
*before* it hands off to a subprocess: which home the child will see, what lands
in the activation file, what `evidence` captures, and what `down` removes.

Not collected by the repository suite: `pyproject.toml` sets
``testpaths = ["tests"]``, and this file is deliberately outside it so that the
verification skill stays self-contained and the kernel's own budgets and
boundary checks are unaffected. Run it explicitly::

    python -m pytest .github/skills/verify-operator/test_control_operator.py -q
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

_SOURCE = Path(__file__).resolve().parent / "control_operator.py"
_spec = importlib.util.spec_from_file_location("verify_operator_control", _SOURCE)
control = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = control
_spec.loader.exec_module(control)


@pytest.fixture
def run(tmp_path) -> Path:
    """A minimal run directory, as `up` would leave it."""
    directory = tmp_path / "run"
    (directory / "home" / "projects" / "guid-1").mkdir(parents=True)
    (directory / "artifacts").mkdir(parents=True)
    (directory / "run.json").write_text(json.dumps({
        "created": "2026-01-01T00:00:00Z", "repo": str(tmp_path / "repo"),
        "guid": "guid-1", "home": str(directory / "home"),
        "artifacts": str(directory / "artifacts"),
    }), encoding="utf-8")
    return directory


# ── the isolation invariant ──────────────────────────────────────────────


def test_the_child_is_told_to_use_the_runs_home(run):
    """The one property the whole harness exists to hold.

    The kernel captures `config.OPERATOR_HOME` at import, so the only thing that
    redirects a child process is this variable being right *before* it starts.
    """
    env = control._env(run)
    assert env["COPILOT_OPERATOR_HOME"] == str(run / "home")


def test_the_child_is_never_pointed_at_the_real_operator_home(run):
    env = control._env(run)
    assert Path(env["COPILOT_OPERATOR_HOME"]) != Path.home() / ".operator"


def test_the_rest_of_the_environment_survives(run, monkeypatch):
    """Inherited, not replaced: PATH has to reach the console scripts."""
    monkeypatch.setenv("VERIFY_OPERATOR_CANARY", "kept")
    env = control._env(run)
    assert env["VERIFY_OPERATOR_CANARY"] == "kept"
    assert "PATH" in env


def test_an_ambient_operator_home_does_not_win(run, monkeypatch):
    """A developer with the variable already set must not redirect the run."""
    monkeypatch.setenv("COPILOT_OPERATOR_HOME", str(Path.home() / ".operator"))
    env = control._env(run)
    assert env["COPILOT_OPERATOR_HOME"] == str(run / "home")


# ── locating the project ─────────────────────────────────────────────────


def test_the_primary_checkout_is_read_from_the_first_porcelain_record():
    """Not `rev-parse --show-toplevel`, which answers the worktree instead."""
    found = control.repo_root(Path(__file__).resolve().parent)
    assert (found / "pyproject.toml").is_file()
    assert (found / "operator_kernel").is_dir()


def test_a_directory_outside_a_checkout_is_refused(tmp_path):
    with pytest.raises(SystemExit):
        control.repo_root(tmp_path)


# ── activation ───────────────────────────────────────────────────────────










# ── seeding ──────────────────────────────────────────────────────────────






















# ── rotating the ledger ──────────────────────────────────────────








# ── padding a journal ────────────────────────────────────────────










# ── evidence ─────────────────────────────────────────────────────────────


def test_evidence_captures_the_state_the_skill_promises(run):
    home = run / "home"
    (home / "operator.log").write_text("log line\n", encoding="utf-8")
    catalog = home / "projects" / "catalog.csv"
    catalog.parent.mkdir(parents=True, exist_ok=True)
    catalog.write_text("repo,guid\n", encoding="utf-8")
    control.cmd_evidence(SimpleNamespace(run=str(run), label="snap"))

    captured = {p.name for p in (run / "artifacts" / "snap").iterdir()}
    assert {"operator.log", "projects__catalog.csv", "MANIFEST.txt"} <= captured


def test_a_nested_state_file_keeps_its_path_in_its_name(run):
    handoff = run / "home" / "projects" / "guid-1" / "handoff"
    handoff.mkdir(parents=True)
    (handoff / "seat-a.md").write_text("# Handoff\n", encoding="utf-8")
    control.cmd_evidence(SimpleNamespace(run=str(run), label="snap"))
    names = {p.name for p in (run / "artifacts" / "snap").iterdir()}
    assert "projects__guid-1__handoff__seat-a.md" in names


def test_doctor_checks_every_console_script_the_harness_drives(run):
    """A PATH check that skips a driven entry point reports healthy about a
    machine that cannot run the recipe. `operator` was missed when the front
    door became drivable, which Reviewer B caught."""
    source = _SOURCE.read_text(encoding="utf-8")
    checked = source.split('for name in (', 1)[1].split(')', 1)[0]
    for name in ("operator",):
        assert f'"{name}"' in checked, (name, checked)


def test_evidence_captures_both_halves_of_a_handoff(run):
    """The file and the marker, because either alone is ambiguous.

    A handoff file with no marker is a checkpoint. A marker with no file is a
    session that ended leaving nothing. A proof that captures one of the two
    cannot tell those apart, which is exactly the distinction the recipe for
    `--no-restart` rests on.
    """
    handoff = run / "home" / "projects" / "guid-1" / "handoff"
    handoff.mkdir(parents=True)
    (handoff / "seat-a.md").write_text("# Handoff\n", encoding="utf-8")
    (run / "home" / "restart").mkdir(parents=True, exist_ok=True)
    (run / "home" / "restart" / "seat-a").touch()

    control.cmd_evidence(SimpleNamespace(run=str(run), label="snap"))
    names = {p.name for p in (run / "artifacts" / "snap").iterdir()}
    assert "projects__guid-1__handoff__seat-a.md" in names
    assert "restart__seat-a" in names


def test_the_front_door_runs_from_the_registered_checkout(monkeypatch, run):
    """`operator handoff` resolves its project from the working directory, so
    the driver has to stand in the checkout the catalog knows about."""
    seen = {}

    def fake(run_, label, argv, cwd):
        seen.update(argv=argv, cwd=cwd)
        return 0

    monkeypatch.setattr(control, "_invoke", fake)
    monkeypatch.setattr(control, "_script", lambda name: f"/bin/{name}")
    monkeypatch.setattr(control, "_meta", lambda r: {"repo": str(run / "repo")})
    control.main(["operator", "--run", str(run), "--label", "h",
                  "--", "handoff", "--instance", "alpha", "--status", "done"])
    assert seen["argv"][0] == "/bin/operator", seen["argv"]
    assert seen["argv"][1:] == ["handoff", "--instance", "alpha",
                                "--status", "done"]
    assert seen["cwd"] == run / "repo"


def test_the_front_door_can_be_pointed_somewhere_unregistered(monkeypatch, run,
                                                              tmp_path):
    """The refusal path needs a directory the catalog does not know, and it has
    to be drivable through the transcript rather than by a raw call beside it."""
    seen = {}

    def fake(run_, label, argv, cwd):
        seen.update(cwd=cwd)
        return 0

    monkeypatch.setattr(control, "_invoke", fake)
    monkeypatch.setattr(control, "_script", lambda name: f"/bin/{name}")
    monkeypatch.setattr(control, "_meta", lambda r: {"repo": str(run / "repo")})
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    control.main(["operator", "--run", str(run), "--cwd", str(elsewhere),
                  "--", "handoff", "--instance", "alpha", "--status", "x"])
    assert seen["cwd"] == elsewhere.resolve()


def test_two_labels_do_not_overwrite_each_other(run):
    (run / "home" / "operator.log").write_text("a\n", encoding="utf-8")
    control.cmd_evidence(SimpleNamespace(run=str(run), label="before"))
    (run / "home" / "operator.log").write_text("a\nb\n", encoding="utf-8")
    control.cmd_evidence(SimpleNamespace(run=str(run), label="after"))
    before = (run / "artifacts" / "before" / "operator.log").read_text("utf-8")
    after = (run / "artifacts" / "after" / "operator.log").read_text("utf-8")
    assert before != after


def test_the_documented_state_globs_are_all_present():
    """SKILL.md lists these by name; a silent removal would shrink every proof."""
    for promised in ("operator.log", "projects/catalog.csv",
                     "projects/*/handoff/*.md", "restart/*"):
        assert promised in control.STATE_GLOBS


# ── teardown ─────────────────────────────────────────────────────────────


def test_teardown_removes_the_instance(run):
    control.cmd_down(SimpleNamespace(run=str(run)))
    assert not (run / "home").exists()


def test_teardown_never_eats_the_evidence(run):
    (run / "artifacts" / "transcript.md").write_text("proof\n", encoding="utf-8")
    control.cmd_down(SimpleNamespace(run=str(run)))
    assert (run / "artifacts" / "transcript.md").read_text("utf-8") == "proof\n"


def test_teardown_twice_is_not_an_error(run):
    assert control.cmd_down(SimpleNamespace(run=str(run))) == 0
    assert control.cmd_down(SimpleNamespace(run=str(run))) == 0


# ── argument handling ────────────────────────────────────────────────────


def test_flags_after_the_separator_reach_the_console_script(monkeypatch):
    """`-- list --help` must arrive as the user typed it, without the `--`."""
    seen = {}

    def fake(run, label, argv, cwd):
        seen["argv"] = argv
        return 0

    monkeypatch.setattr(control, "_invoke", fake)
    monkeypatch.setattr(control, "_script", lambda name: name)
    monkeypatch.setattr(control, "_meta", lambda r: {"repo": str(r)})
    control.main(["operator", "--run", "r", "--", "list", "--help"])
    assert seen["argv"][-2:] == ["list", "--help"]
    assert "--" not in seen["argv"]








def test_addressing_a_run_that_was_never_created_is_refused(tmp_path):
    with pytest.raises(SystemExit):
        control._meta(tmp_path / "nope")


# ── the launch gate ──────────────────────────────────────────────









