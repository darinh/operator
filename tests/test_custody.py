"""The custody file is the identity handoff is allowed to act on."""
from __future__ import annotations

import json

import custody
import op
import process_identity
import process_tree


def test_write_records_pid_token_and_session(tmp_path, monkeypatch):
    monkeypatch.setattr(process_identity, "process_start_token",
                        lambda pid: "win:111")
    path = tmp_path / "op.custody.json"
    custody.write(path, 424242, 4)
    assert json.loads(path.read_text(encoding="utf-8")) == {
        "pid": 424242, "start": "win:111", "session": 4}
    assert custody.read(path) == custody.Custody(424242, "win:111", 4)


def test_read_refuses_a_file_that_is_not_a_custody_record(tmp_path):
    path = tmp_path / "op.custody.json"
    path.write_text("{", encoding="utf-8")
    assert custody.read(path) is None
    path.write_text(json.dumps({"pid": True, "start": "x", "session": 1}),
                    encoding="utf-8")
    assert custody.read(path) is None


def test_identify_returns_the_one_operator_whose_token_matches(
        tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    import operators
    record = operators.create("alpha", tmp_path)
    op.Instance(record.id).custody_file.write_text(
        json.dumps({"pid": 424242, "start": "win:111", "session": 4}),
        encoding="utf-8")
    monkeypatch.setattr(process_tree, "ancestry", lambda pid: [424242])
    monkeypatch.setattr(process_identity, "process_start_token",
                        lambda pid: "win:111")
    found = custody.identify(os_pid())
    assert found[0].id == record.id
    assert found[1] == 4


def os_pid() -> int:
    import os
    return os.getpid()


def _session(tmp_path, monkeypatch, name, pid, session=1, token="win:111"):
    import operators
    monkeypatch.chdir(tmp_path)
    record = operators.create(name, tmp_path)
    op.Instance(record.id).custody_file.write_text(
        json.dumps({"pid": pid, "start": token, "session": session}),
        encoding="utf-8")
    return record


def test_caller_is_the_agent_whose_copilot_is_an_ancestor(tmp_path, monkeypatch):
    record = _session(tmp_path, monkeypatch, "alpha", 424242, session=3)
    monkeypatch.setattr(process_tree, "ancestry", lambda pid: [77, 424242, 1])
    monkeypatch.setattr(process_identity, "process_start_token", lambda pid: "win:111")
    who = custody.caller(os_pid())
    assert who == custody.Agent(record, 3, 424242)


def test_caller_is_a_person_when_no_session_is_an_ancestor(tmp_path, monkeypatch):
    _session(tmp_path, monkeypatch, "alpha", 424242)
    monkeypatch.setattr(process_tree, "ancestry", lambda pid: [77, 1])
    monkeypatch.setattr(process_identity, "process_start_token", lambda pid: "win:111")
    assert custody.caller(os_pid()) == custody.Human(77)


def test_caller_is_a_person_when_the_recorded_pid_was_reused(tmp_path, monkeypatch):
    _session(tmp_path, monkeypatch, "alpha", 424242)
    monkeypatch.setattr(process_tree, "ancestry", lambda pid: [77, 424242])
    monkeypatch.setattr(process_identity, "process_start_token", lambda pid: "win:999")
    assert custody.caller(os_pid()) == custody.Human(77)


def test_a_person_is_named_by_what_ran_operator_exe_not_by_the_launcher(
        tmp_path, monkeypatch):
    import sys
    _session(tmp_path, monkeypatch, "alpha", 424242)
    monkeypatch.setattr(process_tree, "ancestry", lambda pid: [61, 62, 63, 1])
    monkeypatch.setattr(process_tree, "IS_WINDOWS", True)
    monkeypatch.setattr(process_tree, "_win_table", lambda: {
        61: (62, "python.exe"), 62: (63, "operator.exe"), 63: (1, "pwsh.exe")})
    monkeypatch.setattr(sys, "argv", [r"C:\venv\Scripts\operator"])
    assert custody.caller(os_pid()) == custody.Human(63)


def test_two_sessions_in_one_ancestry_is_a_refusal(tmp_path, monkeypatch):
    _session(tmp_path, monkeypatch, "outer", 500)
    _session(tmp_path, monkeypatch, "inner", 600)
    monkeypatch.setattr(process_tree, "ancestry", lambda pid: [77, 600, 90, 500])
    monkeypatch.setattr(process_identity, "process_start_token", lambda pid: "win:111")
    assert custody.caller(os_pid()) == "more than one operator matches this process"


def test_two_operators_claiming_one_process_is_a_refusal(tmp_path, monkeypatch):
    _session(tmp_path, monkeypatch, "alpha", 500)
    _session(tmp_path, monkeypatch, "bravo", 500)
    monkeypatch.setattr(process_tree, "ancestry", lambda pid: [77, 500])
    monkeypatch.setattr(process_identity, "process_start_token", lambda pid: "win:111")
    assert custody.caller(os_pid()) == "more than one operator matches this process"


def test_handoff_refusals_read_as_they_did_before_callers_had_kinds(
        tmp_path, monkeypatch):
    import operators
    monkeypatch.setattr(process_identity, "process_start_token", lambda pid: "win:111")
    monkeypatch.setattr(process_tree, "ancestry", lambda pid: None)
    assert custody.identify(1) == "operator handoff: could not read the process table"
    monkeypatch.setattr(process_tree, "ancestry", lambda pid: [500])
    readable = operators.all_operators
    monkeypatch.setattr(operators, "all_operators", lambda: None)
    assert custody.identify(1) == "operator handoff: could not read operators"
    monkeypatch.setattr(operators, "all_operators", readable)
    assert custody.identify(1) == (
        "operator handoff: this process is not inside an operator session")
    _session(tmp_path, monkeypatch, "alpha", 500)
    _session(tmp_path, monkeypatch, "bravo", 500)
    assert custody.identify(1) == (
        "operator handoff: more than one operator matches this process")
