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


def test_enabling_writes_the_shape_the_kernel_requires(run, capsys):
    control.cmd_enable(SimpleNamespace(run=str(run), extension="seat-watch",
                                       setting=["failures=3"]))
    config = json.loads((run / "home" / "extensions.json").read_text("utf-8"))
    assert config["seat-watch"] == {"enabled": True, "failures": 3}


def test_a_setting_is_json_typed_not_stringified(run):
    control.cmd_enable(SimpleNamespace(run=str(run), extension="x",
                                       setting=["n=2", "flag=true", "word=hi"]))
    entry = json.loads((run / "home" / "extensions.json").read_text("utf-8"))["x"]
    assert entry["n"] == 2 and entry["flag"] is True and entry["word"] == "hi"


def test_enabling_a_second_extension_keeps_the_first(run):
    control.cmd_enable(SimpleNamespace(run=str(run), extension="a", setting=None))
    control.cmd_enable(SimpleNamespace(run=str(run), extension="b", setting=None))
    config = json.loads((run / "home" / "extensions.json").read_text("utf-8"))
    assert set(config) == {"a", "b"}


def test_a_corrupt_activation_file_is_replaced_rather_than_crashing(run):
    """The harness must still be able to set up a run after a corrupt-config test."""
    (run / "home" / "extensions.json").write_text("{not json", encoding="utf-8")
    control.cmd_enable(SimpleNamespace(run=str(run), extension="a", setting=None))
    config = json.loads((run / "home" / "extensions.json").read_text("utf-8"))
    assert config["a"]["enabled"] is True


# ── seeding ──────────────────────────────────────────────────────────────


def test_ledger_records_are_appended_one_per_line(run):
    control.cmd_seed_ledger(SimpleNamespace(
        run=str(run), records=None,
        record=['{"event":"session_exit","instance":"s"}', '{"event":"other"}']))
    lines = (run / "home" / "trace.jsonl").read_text("utf-8").strip().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["event"] == "session_exit"


def test_a_seeded_record_gets_a_timestamp_it_did_not_supply(run):
    control.cmd_seed_ledger(SimpleNamespace(run=str(run), records=None,
                                            record=['{"event":"e"}']))
    record = json.loads((run / "home" / "trace.jsonl").read_text("utf-8").strip())
    assert record["ts"].endswith("Z")


def test_a_supplied_timestamp_is_not_overwritten(run):
    control.cmd_seed_ledger(SimpleNamespace(
        run=str(run), records=None, record=['{"event":"e","ts":"2020-01-01T00:00:00Z"}']))
    record = json.loads((run / "home" / "trace.jsonl").read_text("utf-8").strip())
    assert record["ts"] == "2020-01-01T00:00:00Z"


def test_seeding_nothing_is_refused_rather_than_silently_doing_nothing(run):
    with pytest.raises(SystemExit):
        control.cmd_seed_ledger(SimpleNamespace(run=str(run), records=None,
                                                record=None))


def test_seeding_appends_rather_than_replacing(run):
    for _ in range(2):
        control.cmd_seed_ledger(SimpleNamespace(run=str(run), records=None,
                                                record=['{"event":"e"}']))
    lines = (run / "home" / "trace.jsonl").read_text("utf-8").strip().splitlines()
    assert len(lines) == 2


def test_a_seeded_proposal_is_attributed(run):
    control.cmd_seed_queue(SimpleNamespace(run=str(run), extension="fixture-x",
                                           record=None))
    record = json.loads((run / "home" / "proposals.jsonl").read_text("utf-8").strip())
    assert record["extension"] == "fixture-x"
    assert record["ts"].endswith("Z")


# ── evidence ─────────────────────────────────────────────────────────────


def test_evidence_captures_the_state_the_skill_promises(run):
    home = run / "home"
    (home / "trace.jsonl").write_text('{"event":"e"}\n', encoding="utf-8")
    (home / "proposals.jsonl").write_text('{"extension":"x"}\n', encoding="utf-8")
    (home / "extensions.json").write_text("{}", encoding="utf-8")
    (home / "operator.log").write_text("log line\n", encoding="utf-8")
    control.cmd_evidence(SimpleNamespace(run=str(run), label="snap"))

    captured = {p.name for p in (run / "artifacts" / "snap").iterdir()}
    assert {"trace.jsonl", "proposals.jsonl", "extensions.json",
            "operator.log", "MANIFEST.txt"} <= captured


def test_a_nested_state_file_keeps_its_path_in_its_name(run):
    journal = run / "home" / "projects" / "guid-1" / "journal"
    journal.mkdir(parents=True)
    (journal / "seat-a.jsonl").write_text('{"id":"1"}\n', encoding="utf-8")
    control.cmd_evidence(SimpleNamespace(run=str(run), label="snap"))
    names = {p.name for p in (run / "artifacts" / "snap").iterdir()}
    assert "projects__guid-1__journal__seat-a.jsonl" in names


def test_two_labels_do_not_overwrite_each_other(run):
    (run / "home" / "trace.jsonl").write_text("a\n", encoding="utf-8")
    control.cmd_evidence(SimpleNamespace(run=str(run), label="before"))
    (run / "home" / "trace.jsonl").write_text("a\nb\n", encoding="utf-8")
    control.cmd_evidence(SimpleNamespace(run=str(run), label="after"))
    before = (run / "artifacts" / "before" / "trace.jsonl").read_text("utf-8")
    after = (run / "artifacts" / "after" / "trace.jsonl").read_text("utf-8")
    assert before != after


def test_the_documented_state_globs_are_all_present():
    """SKILL.md lists these by name; a silent removal would shrink every proof."""
    for promised in ("trace.jsonl", "proposals.jsonl", "proposals.handled.jsonl",
                     "fleet-failures.jsonl", "fleet-tail.json", "extensions.json",
                     "operator.log", "extensions/*.json", "projects/catalog.csv",
                     "projects/*/journal/*.jsonl"):
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
    """`-- run --rounds 1` must arrive as the user typed it, without the `--`."""
    seen = {}

    def fake(run, label, argv, cwd):
        seen["argv"] = argv
        return 0

    monkeypatch.setattr(control, "_invoke", fake)
    monkeypatch.setattr(control, "_script", lambda name: name)
    control.main(["fleet", "--run", "r", "--", "run", "--rounds", "1"])
    assert seen["argv"][-3:] == ["run", "--rounds", "1"]
    assert "--" not in seen["argv"]


def test_the_run_home_is_passed_to_operator_fleet(monkeypatch, run):
    seen = {}

    def fake(run_, label, argv, cwd):
        seen["argv"] = argv
        return 0

    monkeypatch.setattr(control, "_invoke", fake)
    monkeypatch.setattr(control, "_script", lambda name: name)
    control.main(["fleet", "--run", str(run), "--", "proposals"])
    assert "--home" in seen["argv"]
    assert seen["argv"][seen["argv"].index("--home") + 1] == str(run / "home")


def test_seat_commands_run_from_the_registered_checkout(monkeypatch, run):
    """The journal is resolved from the working directory, so this is not cosmetic."""
    seen = {}

    def fake(run_, label, argv, cwd):
        seen["cwd"] = cwd
        return 0

    monkeypatch.setattr(control, "_invoke", fake)
    monkeypatch.setattr(control, "_script", lambda name: name)
    control.main(["seat", "--run", str(run), "--", "recall"])
    meta = json.loads((run / "run.json").read_text("utf-8"))
    assert seen["cwd"] == Path(meta["repo"])


def test_addressing_a_run_that_was_never_created_is_refused(tmp_path):
    with pytest.raises(SystemExit):
        control._meta(tmp_path / "nope")
