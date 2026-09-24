"""Every command the preamble advertises has to run, in every state it appears."""
from __future__ import annotations

import re

import op
import pytest

from operator_cli import entry

SEAT = "alpha"

#: The preamble composes different clauses per state, so one rendering does not
#: contain every command it can advertise. Rendering only the default state is
#: how `operator recall` went unexecuted in the first version of this file.
STATES = (
    {},
    {"has_journal": True},
    {"crash_recovery": True},
    {"handoff_waiting": "/tmp/handoff.md"},
    {"handoff_unknown": True},
    {"has_journal": True, "crash_recovery": True},
)


def _render(**kwargs) -> str:
    return op.build_preamble("bench:seat", op.Instance(SEAT), **kwargs)


def _commands(text: str) -> set[str]:
    spans = re.findall(r"`([^`]+)`", text)
    return {s.strip() for s in spans if s.strip().split()[:1] == ["operator"]}


def _advertised() -> list[str]:
    found: set[str] = set()
    for state in STATES:
        found |= _commands(_render(**state))
    return sorted(found)


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


def test_the_states_between_them_advertise_both_memory_commands():
    spans = _advertised()
    assert any("remember" in s for s in spans), spans
    assert any("recall" in s for s in spans), spans


def test_recall_appears_only_once_the_seat_has_something(tmp_path, monkeypatch):
    work = _project(tmp_path, monkeypatch)
    inst = op.Instance(SEAT)
    bare = _commands(op.build_preamble(
        "bench:seat", inst, has_journal=op.seat_has_journal(work, inst.id)))
    assert not any("recall" in c for c in bare)

    assert entry.main(["remember", "--instance", SEAT, "--kind", "gotcha", "x"]) == 0
    after = _commands(op.build_preamble(
        "bench:seat", inst, has_journal=op.seat_has_journal(work, inst.id)))
    assert any("recall" in c for c in after), (
        "a seat that has accumulated memory is never told to read it")


@pytest.mark.parametrize("template", _advertised())
def test_every_advertised_command_runs(template, tmp_path, monkeypatch):
    """A seat told to run a command that exits 2 has been handed a dead
    instruction at the one moment it was listening."""
    _project(tmp_path, monkeypatch)
    argv = template.replace('\\"...\\"', "note").replace('"..."', "note").split()[1:]
    assert entry.main(argv) != 2, (
        f"the preamble advertises `{template}`, which the CLI does not accept")


def test_the_parametrisation_is_not_silently_empty():
    """An empty parametrize list runs zero cases and still reports success."""
    assert len(_advertised()) >= 2
