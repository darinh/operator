"""Handoff may act only for the operator whose copilot is an ancestor."""
from __future__ import annotations

import json

import op
import paths
import process_identity
import process_tree
from operator_cli import entry as cli
from operator_cli.project import ensure_registered


def _project(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert ensure_registered(tmp_path)[0] == 0
    return tmp_path


def _operator(work, name):
    import operators
    return operators.create(name, work)


def _plant(record, pid, token, session):
    op.Instance(record.id).custody_file.write_text(
        json.dumps({"pid": pid, "start": token, "session": session}),
        encoding="utf-8")


def _nothing_written(work, *records):
    assert list(paths.projects_root().rglob("*.md")) == []
    for record in records:
        assert not op.restart_marker_for(record.id).exists()


def test_a_handoff_from_inside_a_writes_as_file_and_as_marker(
        tmp_path, monkeypatch, capsys):
    work = _project(tmp_path, monkeypatch)
    record = _operator(work, "alpha")
    _plant(record, 424242, "win:111", 4)
    monkeypatch.setattr(process_tree, "ancestry", lambda pid: [424242])
    monkeypatch.setattr(process_identity, "process_start_token",
                        lambda pid: "win:111" if pid == 424242 else None)
    elsewhere = tmp_path / "not-the-repo"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    assert cli.main(["handoff", "--status", "landed it", "--next", "ship it"]) == 0
    written = paths.project_handoff_file(work, record.id)
    assert "landed it" in written.read_text(encoding="utf-8")
    assert not list(elsewhere.rglob("*.md"))
    assert json.loads(op.restart_marker_for(record.id).read_text(encoding="utf-8")) == {
        "id": record.id, "session": 4}
    assert f"restart requested for {record.id}" in capsys.readouterr().out


def test_a_forged_instance_from_inside_a_writes_nothing(
        tmp_path, monkeypatch, capsys):
    work = _project(tmp_path, monkeypatch)
    alpha = _operator(work, "alpha")
    beta = _operator(work, "beta")
    _plant(alpha, 424242, "win:111", 4)
    monkeypatch.setattr(process_tree, "ancestry", lambda pid: [424242])
    monkeypatch.setattr(process_identity, "process_start_token",
                        lambda pid: "win:111")
    assert cli.main(["handoff", "--instance", "beta", "--status", "nope"]) == 2
    err = capsys.readouterr().err
    assert "this session is alpha, not beta" in err
    _nothing_written(work, alpha, beta)


def test_a_plain_terminal_writes_nothing(tmp_path, monkeypatch, capsys):
    work = _project(tmp_path, monkeypatch)
    record = _operator(work, "alpha")
    monkeypatch.setattr(process_tree, "ancestry", lambda pid: [999001])
    assert cli.main(["handoff", "--status", "nope"]) == 2
    assert "not inside an operator session" in capsys.readouterr().err
    _nothing_written(work, record)


def test_a_reused_pid_with_a_different_start_token_writes_nothing(
        tmp_path, monkeypatch, capsys):
    work = _project(tmp_path, monkeypatch)
    record = _operator(work, "alpha")
    _plant(record, 424242, "win:111", 4)
    monkeypatch.setattr(process_tree, "ancestry", lambda pid: [424242])
    monkeypatch.setattr(process_identity, "process_start_token",
                        lambda pid: "win:222")
    assert cli.main(["handoff", "--status", "nope"]) == 2
    assert "not inside an operator session" in capsys.readouterr().err
    _nothing_written(work, record)


def test_two_matching_operators_write_nothing(tmp_path, monkeypatch, capsys):
    work = _project(tmp_path, monkeypatch)
    alpha = _operator(work, "alpha")
    beta = _operator(work, "beta")
    _plant(alpha, 111, "win:1", 2)
    _plant(beta, 222, "win:2", 3)
    monkeypatch.setattr(process_tree, "ancestry", lambda pid: [111, 222])

    def token(pid):
        return {111: "win:1", 222: "win:2"}.get(pid)

    monkeypatch.setattr(process_identity, "process_start_token", token)
    assert cli.main(["handoff", "--status", "nope"]) == 2
    assert "more than one operator matches" in capsys.readouterr().err
    _nothing_written(work, alpha, beta)


def test_an_unreadable_process_table_writes_nothing(tmp_path, monkeypatch, capsys):
    work = _project(tmp_path, monkeypatch)
    record = _operator(work, "alpha")
    _plant(record, 424242, "win:111", 4)
    monkeypatch.setattr(process_tree, "ancestry", lambda pid: None)
    assert cli.main(["handoff", "--status", "nope"]) == 2
    assert "could not read the process table" in capsys.readouterr().err
    _nothing_written(work, record)


def test_no_restart_still_requires_the_calling_session(
        tmp_path, monkeypatch, capsys):
    work = _project(tmp_path, monkeypatch)
    record = _operator(work, "alpha")
    monkeypatch.setattr(process_tree, "ancestry", lambda pid: [])
    assert cli.main(["handoff", "--status", "midway", "--no-restart"]) == 2
    assert "not inside an operator session" in capsys.readouterr().err
    _nothing_written(work, record)
