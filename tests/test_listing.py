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


def test_list_shows_each_child_under_its_parent(tmp_path, monkeypatch, capsys):
    import supervisor_control
    work = tmp_path / "repo"
    work.mkdir()
    top = operators.create("alpha", work)
    child = operators.create("bravo", work, parent=top.id)
    operators.create("charlie", work, parent=child.id)
    operators.create("delta", work, parent="op-gone0000")
    monkeypatch.setattr(supervisor_control, "active_instances", lambda: [])
    assert listing.list_instances() == 0
    assert capsys.readouterr().out == (
        "Running:\n"
        "  (none)\n"
        "Offline:\n"
        f"  1. alpha  ({work.resolve()})\n"
        f"  2.   bravo  ({work.resolve()})  child of alpha\n"
        f"  3.     charlie  ({work.resolve()})  child of bravo\n"
        f"  4. delta  ({work.resolve()})\n"
    )


def test_list_with_no_records_says_how_to_start(capsys):
    assert listing.list_instances() == 0
    assert capsys.readouterr().out == (
        "No operators yet. Start one with: operator start\n"
    )


def test_list_says_mail_waits_even_when_its_sender_was_deleted(capsys):
    import mail
    mail.post(operators.HUMAN, {"from": "op-gone0000", "from_name": "gone",
                                "to": operators.HUMAN, "relation": "an operator you started",
                                "text": "last words", "sent": "x"})
    assert listing.list_instances() == 0
    assert capsys.readouterr().out == (
        "No operators yet. Start one with: operator start\n"
        "1 message(s) waiting. Read them with: operator inbox\n"
    )


def test_an_operator_listing_is_not_told_about_the_persons_mail(
        tmp_path, monkeypatch, capsys):
    """An operator's `inbox` reads its own box, so the person's count would
    send it looking for mail it cannot read."""
    import mail
    from test_handoff import _seat
    alpha = operators.create("alpha", tmp_path)
    mail.post(operators.HUMAN, {"from": alpha.id, "from_name": "alpha",
                                "to": operators.HUMAN, "relation": "an operator you started",
                                "text": "done", "sent": "x"})
    _seat(monkeypatch, alpha)
    assert listing.list_instances() == 0
    assert "waiting" not in capsys.readouterr().out
