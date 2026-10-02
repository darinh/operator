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
