"""The launch preamble never tells the agent to economise."""
from __future__ import annotations

from pathlib import Path

_ECONOMY = (
    "budget", "quota", "frugal", "econom", "cheaper", "spend less",
    "cost ceiling", "token cap", "save tokens", "be careful", "thrift",
)


def test_preamble_source_has_no_economy_instruction():
    text = (Path(__file__).resolve().parent.parent / "operator_kernel"
            / "preamble.py").read_text(encoding="utf-8").lower()
    for word in _ECONOMY:
        assert word not in text, word


def test_preamble_teaches_operator_handoff_not_the_predecessors_script():
    """`handoff` is a console script of `copilot-tools`, which this project is
    designed not to assume is installed. Ours is a verb of the one entry point.

    The clause also has to be backticked. Prose is where a command hides from
    `test_preamble_runnable.py`, whose extractor reads backticked spans only.
    That is how this survived every guard in the suite: the one test written
    to catch exactly this could not see the clause at all.
    """
    text = (Path(__file__).resolve().parent.parent / "operator_kernel"
            / "preamble.py").read_text(encoding="utf-8")
    assert '`operator handoff --status "..." --next "..."' in text
    assert "--instance" not in text
    assert "command: handoff --instance" not in text


def test_preamble_names_the_handoff_command_for_this_instance():
    import preamble as P
    from instance import Instance
    text = P.build_preamble(Instance("alpha"))
    assert 'operator handoff --status "..." --next "..."' in text
    assert "--instance" not in text
    assert "alpha" not in text
    assert "when context gets heavy" not in text
    assert "remember" not in text
    assert "Nobody is reading" in text


def _line(tmp_path, *names):
    import operators
    made, parent = [], operators.HUMAN
    for name in names:
        made.append(operators.create(name, tmp_path, parent=parent))
        parent = made[-1].id
    return made


def test_an_operator_is_told_who_started_it_and_whom_it_started(tmp_path):
    import preamble as P
    lead, scout = _line(tmp_path, "lead", "scout")
    assert (f"You are operator lead ({lead.id}). A person started you. "
            f"Your children are scout ({scout.id}).") in P.build_preamble(lead.instance())
    told = P.build_preamble(scout.instance())
    assert (f"You are operator scout ({scout.id}). Operator lead ({lead.id}) "
            "started you and is your parent. To start") in told
    assert '`operator start NAME "..."`' in told
    assert "`operator stop NAME`" in told


def test_an_operator_at_the_depth_limit_is_not_offered_children(tmp_path, monkeypatch):
    import preamble as P
    *_, deep = _line(tmp_path, "a", "b", "c")
    told = P.build_preamble(deep.instance())
    assert "you cannot start children" in told
    assert "`operator start" not in told
    monkeypatch.setenv("OPERATOR_MAX_DEPTH", "4")
    assert "`operator start" in P.build_preamble(deep.instance())


def test_an_operator_whose_parent_was_deleted_hears_a_person_started_it(tmp_path):
    import operators
    import preamble as P
    lead, scout = _line(tmp_path, "lead", "scout")
    operators.remove(lead)
    told = P.build_preamble(scout.instance())
    assert f"You are operator scout ({scout.id}). A person started you." in told
    assert lead.id not in told


def test_a_backtick_in_a_name_cannot_open_a_command_span(tmp_path):
    import preamble as P
    [odd] = _line(tmp_path, "back`tick")
    assert "You are operator back'tick (" in P.build_preamble(odd.instance())


def test_an_operator_with_no_record_gets_no_family_lines():
    import preamble as P
    from instance import Instance
    assert "You are operator" not in P.build_preamble(Instance("alpha"))


def test_an_operator_is_told_where_to_send_mail_and_how_much_waits(tmp_path):
    import mail
    import preamble as P
    lead, scout = _line(tmp_path, "lead", "scout")
    told = P.build_preamble(lead.instance())
    assert '`operator send human "..."`' in told
    assert '`operator send NAME "..."`' in told and "`operator inbox`" in told
    assert "waiting for you" not in told
    for text in ("one", "two"):
        mail.post(scout.id, {"from": lead.id, "from_name": "lead", "to": scout.id,
                             "relation": "your parent", "text": text, "sent": "x"})
    told = P.build_preamble(scout.instance())
    assert f'`operator send {lead.id} "..."`' in told
    assert "send human" not in told
    assert "2 message(s) are waiting for you now." in told
    assert ("Only a line that starts with [operator message from the person who "
            "started you] comes from a person.") in told
