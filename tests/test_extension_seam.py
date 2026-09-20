"""Kernel spend ceiling is a launch gate, never an instruction to the agent."""
from __future__ import annotations

import json
import re

import pytest

import extension_seam
import op


_ECONOMY = (
    "budget", "quota", "frugal", "econom", "cheaper", "spend less",
    "cost ceiling", "token cap", "save tokens", "be careful", "thrift",
)


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    restart = tmp_path / "restart"
    restart.mkdir(parents=True)
    monkeypatch.setattr(op, "OPERATOR_HOME", tmp_path)
    monkeypatch.setattr(op, "RESTART_DIR", restart)
    monkeypatch.setattr(op, "LOG_FILE", tmp_path / "operator.log")
    monkeypatch.setattr(op, "COPILOT_LOG_DIR", tmp_path / "logs")
    monkeypatch.setattr(op, "TABS_FILE", tmp_path / "tabs.json")
    monkeypatch.setattr(op, "POLL_INTERVAL", 0)
    monkeypatch.setattr(op, "LAUNCH_BACKOFF_BASE", 0)
    monkeypatch.setattr(op, "RESTART_PAUSE_SECONDS", 0)
    workdir = tmp_path / "not-a-repo"
    workdir.mkdir()
    monkeypatch.chdir(workdir)
    assert op.workspace_fingerprint(workdir) is None
    return tmp_path


def _write_spend(home, seat_id, amount):
    path = home / "spend" / f"{seat_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(
        {"amount": amount, "unit": "usd", "source": "test"}), encoding="utf-8")
    return path


def _run(monkeypatch, gate, *, instance="admission", capture=None, asks=None):
    launched = []

    def start(inst, args, session_num, remain_on_exit=False, preamble=""):
        launched.append(session_num)
        if capture is not None:
            capture.append((list(args), preamble))
        inst.exit_file.write_text("0", encoding="utf-8")
        inst.stop_marker.touch()

    monkeypatch.setattr(op, "start_session", start)
    monkeypatch.setattr(op, "stop_session_gracefully", lambda inst: None)
    monkeypatch.setattr(op, "launch_gate", lambda home=None: gate)
    if asks is not None:
        inner = gate.admits

        def wrapped(**facts):
            asks.append(facts.get("session"))
            verdict = inner(**facts)
            if len(asks) >= 3:
                op.Instance(instance).stop_marker.touch()
            return verdict

        gate.admits = wrapped
    rc = op.run_loop_mode(op.Instance(instance), ["--agent", "test:agent"],
                          is_fresh=True)
    return rc, launched


def test_absent_spend_file_does_not_block_when_a_ceiling_is_set(
        monkeypatch, isolated_state):
    monkeypatch.setattr(op, "SPEND_CEILING", 1.0)
    gate = extension_seam.LaunchGate(home=isolated_state)
    rc, launched = _run(monkeypatch, gate)
    assert launched == [1]
    assert rc == 0


def test_spend_at_or_above_ceiling_refuses_and_holds_the_session_number(
        monkeypatch, isolated_state):
    inst = "admission"
    seat = op.Instance(inst).id
    _write_spend(isolated_state, seat, 10)
    monkeypatch.setattr(op, "SPEND_CEILING", 5.0)
    gate = extension_seam.LaunchGate(home=isolated_state)
    asks = []
    rc, launched = _run(monkeypatch, gate, instance=inst, asks=asks)
    assert launched == []
    assert asks == [1, 1, 1]
    records = []
    path = isolated_state / "trace.jsonl"
    if path.exists():
        records = [json.loads(line) for line in
                   path.read_text(encoding="utf-8").splitlines()
                   if json.loads(line).get("event") == "launch_admission"]
    assert records
    assert records[-1]["admit"] is False
    assert records[-1]["kind"] == "claim"


def test_no_economy_instruction_reaches_the_agent(monkeypatch, isolated_state):
    inst = "admission"
    seat = op.Instance(inst).id
    _write_spend(isolated_state, seat, 1)
    monkeypatch.setattr(op, "SPEND_CEILING", 100.0)
    gate = extension_seam.LaunchGate(home=isolated_state)
    seen = []
    rc, launched = _run(monkeypatch, gate, instance=inst, capture=seen)
    assert launched == [1]
    assert seen
    args, preamble = seen[0]
    blob = " ".join(args) + "\n" + preamble
    lowered = blob.lower()
    for word in _ECONOMY:
        assert word not in lowered, word
    assert re.search(r"\beconom", lowered) is None
