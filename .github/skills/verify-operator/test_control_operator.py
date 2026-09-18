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
                                           record=None, abandoned=False, pad_to_bytes=None))
    record = json.loads((run / "home" / "proposals.jsonl").read_text("utf-8").strip())
    assert record["extension"] == "fixture-x"
    assert record["ts"].endswith("Z")


def test_an_abandoned_batch_is_not_written_to_the_live_queue(run):
    """The orphan a crashed drain leaves behind, which the next drain adopts."""
    control.cmd_seed_queue(SimpleNamespace(run=str(run), extension="fixture-x",
                                           record=None, abandoned=True, pad_to_bytes=None))
    home = run / "home"
    assert not (home / "proposals.jsonl").exists()
    orphans = list(home.glob("proposals.draining.*.jsonl"))
    assert len(orphans) == 1
    assert json.loads(orphans[0].read_text("utf-8").strip())["extension"] == "fixture-x"


def test_two_abandoned_batches_do_not_collide(run):
    """`_claim` keys orphans on pid AND a nanosecond stamp; so must the fixture."""
    for _ in range(2):
        control.cmd_seed_queue(SimpleNamespace(run=str(run), extension="x",
                                               record=None, abandoned=True, pad_to_bytes=None))
    assert len(list((run / "home").glob("proposals.draining.*.jsonl"))) == 2


def test_padding_reaches_the_size_at_which_the_host_refuses(run):
    """`_append_proposal` compares st_size, so only bulk matters."""
    target = 200_000
    control.cmd_seed_queue(SimpleNamespace(run=str(run), extension="filler",
                                           record=None, abandoned=False,
                                           pad_to_bytes=target))
    assert (run / "home" / "proposals.jsonl").stat().st_size >= target


def test_padding_still_leaves_parseable_lines(run):
    """A queue of unreadable bulk would prove the wrong refusal."""
    control.cmd_seed_queue(SimpleNamespace(run=str(run), extension="filler",
                                           record=None, abandoned=False,
                                           pad_to_bytes=50_000))
    body = (run / "home" / "proposals.jsonl").read_text(encoding="utf-8")
    for line in body.splitlines():
        if line.strip():
            json.loads(line)


# ── rotating the ledger ──────────────────────────────────────────


def test_rotating_renames_rather_than_copying(run):
    """The appender rotates by rename; a copy would leave two live files."""
    trace = run / "home" / "trace.jsonl"
    trace.parent.mkdir(parents=True, exist_ok=True)
    trace.write_text('{"event":"e"}\n', encoding="utf-8")

    control.cmd_rotate_ledger(SimpleNamespace(run=str(run)))

    assert not trace.exists(), "the live ledger should be gone after a rename"
    assert (run / "home" / "trace.jsonl.1").read_text(encoding="utf-8") == (
        '{"event":"e"}\n')


def test_rotating_twice_replaces_the_previous_rotation(run):
    """`_rotate_if_needed` keeps one `.1` and no more; the fixture must match."""
    trace = run / "home" / "trace.jsonl"
    trace.parent.mkdir(parents=True, exist_ok=True)
    trace.write_text("first\n", encoding="utf-8")
    control.cmd_rotate_ledger(SimpleNamespace(run=str(run)))
    trace.write_text("second\n", encoding="utf-8")
    control.cmd_rotate_ledger(SimpleNamespace(run=str(run)))

    assert (run / "home" / "trace.jsonl.1").read_text(encoding="utf-8") == "second\n"
    assert not list((run / "home").glob("trace.jsonl.2"))


def test_rotating_nothing_is_refused_rather_than_silently_succeeding(run):
    with pytest.raises(SystemExit):
        control.cmd_rotate_ledger(SimpleNamespace(run=str(run)))


# ── padding a journal ────────────────────────────────────────────


def test_journal_padding_does_not_overshoot_its_target(run):
    """An imprecise pad cannot isolate a boundary, which is its whole purpose.

    Two things made it overshoot: a buffered handle, so `stat()` lagged the
    writes, and Windows translating each "\\n" into "\\r\\n", one byte per line
    the count never saw.
    """
    target = 300_000
    control.cmd_seed_journal(SimpleNamespace(run=str(run), seat="cap-seat",
                                             pad_to_bytes=target))
    path = (run / "home" / "projects" / "guid-1" / "journal" / "cap-seat.jsonl")
    assert path.stat().st_size <= target


def test_journal_padding_gets_close_enough_to_be_useful(run):
    """Within one record of the target, or a boundary test cannot be set up."""
    target = 300_000
    control.cmd_seed_journal(SimpleNamespace(run=str(run), seat="cap-seat",
                                             pad_to_bytes=target))
    path = (run / "home" / "projects" / "guid-1" / "journal" / "cap-seat.jsonl")
    assert target - path.stat().st_size < 1000


def test_journal_padding_writes_entries_the_reader_can_parse(run):
    """Padding with junk would prove a refusal caused by the wrong thing."""
    control.cmd_seed_journal(SimpleNamespace(run=str(run), seat="cap-seat",
                                             pad_to_bytes=20_000))
    path = (run / "home" / "projects" / "guid-1" / "journal" / "cap-seat.jsonl")
    lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert lines
    for line in lines:
        record = json.loads(line)
        assert record["instance"] == "cap-seat"
        assert record["verified"] is False


def test_journal_padding_appends_to_what_is_already_there(run):
    """It must extend a real journal, not replace one."""
    path = (run / "home" / "projects" / "guid-1" / "journal" / "cap-seat.jsonl")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"id":"keepme","instance":"cap-seat"}\n', encoding="utf-8")
    control.cmd_seed_journal(SimpleNamespace(run=str(run), seat="cap-seat",
                                             pad_to_bytes=20_000))
    assert "keepme" in path.read_text(encoding="utf-8")


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


def test_the_working_directory_can_be_overridden(monkeypatch, run, tmp_path):
    """Needed to drive the refusal from a directory that is not a project."""
    seen = {}

    def fake(run_, label, argv, cwd):
        seen["cwd"] = cwd
        return 0

    monkeypatch.setattr(control, "_invoke", fake)
    monkeypatch.setattr(control, "_script", lambda name: name)
    elsewhere = tmp_path / "not-a-project"
    elsewhere.mkdir()
    control.main(["seat", "--run", str(run), "--cwd", str(elsewhere),
                  "--", "recall"])
    assert seen["cwd"] == elsewhere.resolve()


def test_addressing_a_run_that_was_never_created_is_refused(tmp_path):
    with pytest.raises(SystemExit):
        control._meta(tmp_path / "nope")

