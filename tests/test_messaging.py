"""`operator send` and `operator inbox`, run as each kind of caller."""
from __future__ import annotations

import os

import pytest

import mail
from operator_cli import entry as cli
from operators import HUMAN
from test_handoff import _seat


@pytest.fixture
def family(tmp_path):
    """alpha and bravo, a person's; scout and sib, alpha's; grand, scout's."""
    import operators
    alpha = operators.create("alpha", tmp_path)
    scout = operators.create("scout", tmp_path, parent=alpha.id)
    return {"alpha": alpha, "scout": scout,
            "sib": operators.create("sib", tmp_path, parent=alpha.id),
            "grand": operators.create("grand", tmp_path, parent=scout.id),
            "bravo": operators.create("bravo", tmp_path)}


def _as(monkeypatch, family, name):
    """Run what follows as operator ``name``'s agent, or as the person."""
    import process_tree
    if name == HUMAN:
        monkeypatch.setattr(process_tree, "ancestry", lambda pid: [pid])
    else:
        _seat(monkeypatch, family[name], pid=1000 + list(family).index(name))


def _posted() -> list:
    return sorted(path for path in mail.box("x").parent.rglob("*.json"))


def test_mail_crosses_only_a_parent_and_child_edge(monkeypatch, capsys, family):
    sent = set()
    for sender in (HUMAN, "alpha", "scout", "grand"):
        _as(monkeypatch, family, sender)
        for target in (HUMAN, *family):
            code = cli.main(["send", target, "hi"])
            assert code in (0, 2), (sender, target, capsys.readouterr())
            if code == 0:
                sent.add((sender, target))
    assert sent == {(HUMAN, "alpha"), (HUMAN, "bravo"),
                    ("alpha", HUMAN), ("alpha", "scout"), ("alpha", "sib"),
                    ("scout", "alpha"), ("scout", "grand"),
                    ("grand", "scout")}
    assert len(_posted()) == len(sent), "a refused send left mail behind"


def test_each_message_says_how_the_sender_is_related(monkeypatch, capsys, family):
    ids = {name: record.id for name, record in family.items()}
    for sender, target, header in (
            (HUMAN, "alpha", "the person who started you"),
            ("alpha", "scout", f"alpha ({ids['alpha']}), your parent"),
            ("scout", "alpha", f"scout ({ids['scout']}), your child"),
            ("alpha", HUMAN, f"alpha ({ids['alpha']}), an operator you started")):
        _as(monkeypatch, family, sender)
        assert cli.main(["send", target, "say", '"hi"', "&", "%USERNAME%"]) == 0
        assert capsys.readouterr().out == f"sent to {target}\n"
        _, message = mail.take(ids.get(target, HUMAN))
        assert mail.line(message) == (
            f'[operator message from {header}] say "hi" & %USERNAME%')


def test_a_message_holds_4000_characters(monkeypatch, capsys, family):
    _as(monkeypatch, family, "alpha")
    assert cli.main(["send", "scout", "x" * 4000]) == 0
    assert cli.main(["send", "scout", "x" * 4001]) == 2
    assert "this one holds 4001" in capsys.readouterr().err
    assert mail.take(family["scout"].id)[1]["text"] == "x" * 4000
    assert len(_posted()) == 1


def test_inbox_prints_the_callers_mail_once(monkeypatch, capsys, family):
    _as(monkeypatch, family, "scout")
    assert cli.main(["send", "alpha", "found 12"]) == 0
    assert cli.main(["send", "grand", "carry on"]) == 0
    _as(monkeypatch, family, "alpha")
    assert cli.main(["send", HUMAN, "all done"]) == 0
    capsys.readouterr()
    assert cli.main(["inbox"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 1 and lines[0].endswith(
        f"[operator message from scout ({family['scout'].id}), your child] found 12")
    assert cli.main(["inbox"]) == 0
    assert capsys.readouterr().out == "No messages.\n"
    _as(monkeypatch, family, HUMAN)
    assert cli.main(["inbox"]) == 0
    assert capsys.readouterr().out.rstrip().endswith("] all done")
    assert mail.waiting(family["grand"].id) == 1, "inbox read someone else's mail"


def test_a_persons_inbox_takes_back_what_an_interrupted_inbox_claimed(
        monkeypatch, capsys, family):
    _as(monkeypatch, family, "alpha")
    assert cli.main(["send", HUMAN, "stranded"]) == 0
    assert mail.take(HUMAN) is not None
    _as(monkeypatch, family, HUMAN)
    capsys.readouterr()
    assert cli.main(["inbox"]) == 0
    assert capsys.readouterr().out.rstrip().endswith("] stranded")


def test_a_send_that_cannot_be_written_says_so(monkeypatch, capsys, family):
    _as(monkeypatch, family, "alpha")
    mail.box(family["scout"].id).parent.mkdir(parents=True, exist_ok=True)
    mail.box(family["scout"].id).write_text("", encoding="utf-8")
    assert cli.main(["send", "scout", "hello"]) == 1
    assert capsys.readouterr().err.startswith("could not send: ")
    assert os.path.isfile(mail.box(family["scout"].id))
