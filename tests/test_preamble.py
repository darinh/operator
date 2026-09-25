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


def test_preamble_teaches_operator_remember_not_operator_seat():
    text = (Path(__file__).resolve().parent.parent / "operator_kernel"
            / "preamble.py").read_text(encoding="utf-8")
    assert "operator-seat remember" not in text
    assert "operator-seat recall" not in text
    assert "operator remember --instance" in text
    assert "operator recall --instance" in text


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
    assert "`operator handoff --instance" in text
    assert "command: handoff --instance" not in text


def test_the_fallback_caution_does_not_promise_a_state_for_every_instance():
    """A clause may not promise a scope the command it names cannot reach.

    This one told an agent that ``operator list`` "reports the same state for
    every instance on this machine". That command walks `active_instances`,
    which drops a managed seat with no live session and no live supervisor, so
    the sentence covered seats it never sees. The narrower claim is the true
    one, and it is still worth making: the point of sending the reader there
    is to show whether its own supervisor is the only one behind.

    Read from the rendered clause rather than from the source, because the
    sentence an agent acts on is the composed one, and both verdicts that
    reach this branch are checked because a reader arrives by two routes.
    """
    import op
    import preamble as P

    for verdict in (op.CODE_UNRECORDED, op.CODE_UNKNOWN):
        clause = P.build_preamble("a:b", op.Instance("alpha"),
                                  code_state=verdict)
        assert "`operator list`" in clause, verdict
        assert "every instance on this machine" not in clause, verdict
