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
    assert ("This is an unattended operator-managed session, created by a "
            f"person. You are operator lead ({lead.id}). "
            f"Your children are scout ({scout.id}).") in P.build_preamble(
                lead.instance())
    told = P.build_preamble(scout.instance())
    assert ("This is an unattended operator-managed session, created by an "
            f"agent. You are operator scout ({scout.id}). Operator lead "
            f"({lead.id}) started you and is your parent.") in told
    assert '`operator start NAME "..."`' in told
    assert "`operator stop NAME`" in told


def test_a_session_leads_with_whether_a_person_or_an_agent_created_it(tmp_path):
    """Whether there is a parent agent to talk to decides what an operator
    can do about anything it cannot settle alone, so it is the first thing
    said rather than the fifth."""
    import preamble as P
    lead, scout = _line(tmp_path, "lead", "scout")
    assert P.build_preamble(lead.instance()).startswith(
        "This is an unattended operator-managed session, created by a person.")
    assert P.build_preamble(scout.instance()).startswith(
        "This is an unattended operator-managed session, created by an agent.")


def test_the_command_is_given_as_the_only_way_to_write_a_handoff():
    """A file written by hand ends no session and reaches no successor. The
    preamble has to rule that out rather than describe the command as one
    way of several."""
    import preamble as P
    from instance import Instance
    text = P.build_preamble(Instance("alpha"))
    assert ("Write a handoff only by running `operator handoff "
            '--status "..." --next "..."`.') in text
    assert "whose agent is handed what you wrote" in text
    assert "Nothing you write by hand reaches anyone." in text


def test_a_child_operator_is_offered_as_one_that_works_independently(tmp_path):
    """Knowing the command is not knowing what it buys. An operator that
    reads `operator start` as bookkeeping never fans work out."""
    import preamble as P
    [lead] = _line(tmp_path, "lead")
    assert ("To start a child operator, which runs unattended in your "
            "checkout and works independently of you, run") in P.build_preamble(
                lead.instance())


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
    assert ("This is an unattended operator-managed session, created by a "
            f"person. You are operator scout ({scout.id}).") in told
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


def test_no_operator_is_told_to_message_the_person_who_started_it(tmp_path):
    """There is no use for mail up to a person yet, and a command an
    operator is shown is attention it does not spend on the work. The verb
    stays in the CLI, so this is about what the preamble advertises."""
    import preamble as P
    from operator_cli import entry
    [lead] = _line(tmp_path, "lead")
    told = P.build_preamble(lead.instance())
    assert "human" not in told
    assert told.count("operator send") == 1, told
    assert "send" in entry.HANDLERS


def test_mail_is_described_as_running_in_both_directions(tmp_path):
    import preamble as P
    lead, scout = _line(tmp_path, "lead", "scout")
    told = P.build_preamble(scout.instance())
    assert (f'To message your parent, run `operator send {lead.id} "..."`, '
            'and a child, `operator send NAME "..."`. They message you the '
            "same way.") in told
