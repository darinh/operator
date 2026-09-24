"""Every command the rendered preamble advertises has to actually run."""
from __future__ import annotations

import re

import op
import pytest

from operator_cli import entry

SEAT = "alpha"


def _rendered(workdir=None) -> str:
    """Composed the way the supervisor composes it.

    `has_journal` is a parameter, not something `build_preamble` probes for
    itself, so a test that omits it renders the empty-journal preamble and
    concludes the read clause is broken. Mirror supervisor.py rather than
    calling the function bare.
    """
    inst = op.Instance(SEAT)
    has = op.seat_has_journal(workdir, inst.id) if workdir is not None else False
    return op.build_preamble("bench:seat", inst, has_journal=has)


def _commands(text: str) -> list[str]:
    """Backticked spans whose first token is the program name.

    Rendered, not read from source. An earlier version parsed the module and
    picked up commands quoted inside comments explaining history, which are
    advertised to nobody.

    Extracted permissively and judged separately. One verb-restricted pattern
    used both to find commands and to decide how many there should be lets a
    misspelled one go missing from both sides while the equality still holds.
    """
    spans = re.findall(r"`([^`]+)`", text)
    return sorted({s.strip() for s in spans if s.strip().split()[:1] == ["operator"]})


def _project(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.setenv("COPILOT_OPERATOR_HOME", str(home))
    monkeypatch.setattr(op, "OPERATOR_HOME", home, raising=False)
    monkeypatch.chdir(work)
    assert entry.main(["project", "register"]) == 0
    return work


def test_a_seat_with_nothing_recorded_is_told_how_to_remember():
    assert any("remember" in c for c in _commands(_rendered()))


def test_recall_appears_only_once_the_seat_has_something(tmp_path, monkeypatch):
    """Conditional on purpose. Telling a seat to recall an empty journal is
    noise at the one moment it is reading carefully."""
    work = _project(tmp_path, monkeypatch)
    assert not any("recall" in c for c in _commands(_rendered(work)))
    assert entry.main(["remember", "--instance", SEAT, "--kind", "gotcha", "x"]) == 0
    assert any("recall" in c for c in _commands(_rendered(work))), (
        "a seat that has accumulated memory is never told to read it")


@pytest.mark.parametrize("template", _commands(_rendered()))
def test_every_advertised_command_runs(template, tmp_path, monkeypatch):
    """Run it, do not parse it.

    The preamble and the CLI parsers drifted once with both files' suites
    green, because nothing executed what the text promised. A seat told to run
    a command that exits 2 has been handed a dead instruction at the one moment
    it was listening.
    """
    _project(tmp_path, monkeypatch)
    argv = template.replace('\\"...\\"', "note").replace('"..."', "note").split()[1:]
    assert entry.main(argv) != 2, (
        f"the preamble advertises `{template}`, which the CLI does not accept")
