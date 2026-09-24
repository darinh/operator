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
