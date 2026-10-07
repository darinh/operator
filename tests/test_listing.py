"""`operator list` names each operator, running and not."""
from __future__ import annotations

import operators
from operator_cli import listing


def test_list_numbers_running_and_offline(tmp_path, monkeypatch, capsys):
    import supervisor_records
    work = tmp_path / "repo"
    work.mkdir()
    running = operators.create("alpha", work)
    offline = operators.create("bravo", work)
    monkeypatch.setattr(listing, "_running_loop_pid",
                        lambda inst: 11 if inst.id == running.id else None)
    monkeypatch.setattr(supervisor_records, "_running_loop_pid",
                        lambda inst: 11 if inst.id == running.id else None)
    import supervisor_control
    monkeypatch.setattr(supervisor_control, "active_instances",
                        lambda: [running.instance()])
    assert listing.list_instances() == 0
    assert capsys.readouterr().out == (
        "Running:\n"
        f"  1. alpha  ({work.resolve()})  pid 11\n"
        "Offline:\n"
        f"  1. bravo  ({work.resolve()})\n"
    )
    assert offline.name == "bravo"


def test_the_menu_splits_operators_the_way_list_does(tmp_path, monkeypatch):
    import supervisor_control
    import supervisor_records
    from operator_cli.entry import _Actions
    work = tmp_path / "repo"
    work.mkdir()
    running = operators.create("alpha", work)
    operators.create("bravo", work)
    monkeypatch.setattr(supervisor_records, "_running_loop_pid",
                        lambda inst: 11 if inst.id == running.id else None)
    monkeypatch.setattr(listing, "_running_loop_pid",
                        lambda inst: 11 if inst.id == running.id else None)
    monkeypatch.setattr(supervisor_control, "active_instances",
                        lambda: [running.instance()])
    on, off, problems = _Actions().sections()
    assert [(o.name, o.label, o.running) for o in on] == [
        ("alpha", f"alpha  ({work.resolve()})  pid 11", True)]
    assert [(o.name, o.label, o.running) for o in off] == [
        ("bravo", f"bravo  ({work.resolve()})", False)]
    assert problems == []


def test_an_empty_heading_says_none(tmp_path, monkeypatch, capsys):
    import supervisor_control
    work = tmp_path / "repo"
    work.mkdir()
    operators.create("only", work)
    monkeypatch.setattr(supervisor_control, "active_instances", lambda: [])
    assert listing.list_instances() == 0
    out = capsys.readouterr().out
    assert "Running:\n  (none)\n" in out
    assert "Offline:\n  1. only  " in out


def test_list_names_each_unreadable_record(tmp_path, capsys):
    work = tmp_path / "repo"
    work.mkdir()
    operators.create("kept", work)
    broken = operators.records_dir() / "op-broken1.json"
    broken.write_text("{not json", encoding="utf-8")
    assert listing.list_instances() == 0
    captured = capsys.readouterr()
    assert captured.err == f"could not read {broken}\n"
    assert "kept" in captured.out


def test_list_with_no_records_says_how_to_start(capsys):
    assert listing.list_instances() == 0
    assert capsys.readouterr().out == (
        "No operators yet. Start one with: operator start\n"
    )
