"""Every command the supervisor advertises to a seat has to run.

The preamble is composed in one file and parsed in another, so the two can
drift while both suites stay green. What is captured here is the text a real
launch hands a seat, not a reconstruction of it: reconstructing the wiring
would keep passing if the supervisor stopped passing an argument.
"""
from __future__ import annotations

import re
import shlex

import op
import pytest

from operator_cli import entry

SEAT = "alpha"


def _launch_preamble(monkeypatch, tmp_path, *, remembered: str = "") -> str:
    """The text one real `run_loop_mode` session hands its seat."""
    from conftest import FakeMux

    home = tmp_path / "home"
    work = tmp_path / "work"
    for d in (home, work):
        d.mkdir(exist_ok=True)
    monkeypatch.setenv("COPILOT_OPERATOR_HOME", str(home))
    monkeypatch.setattr(op, "MUX", FakeMux())
    monkeypatch.setattr(op, "RESTART_DIR", tmp_path / "restart")
    monkeypatch.setattr(op, "OPERATOR_HOME", home)
    monkeypatch.chdir(work)

    assert entry.main(["project", "register"]) == 0
    if remembered:
        assert entry.main(
            ["remember", "--instance", SEAT, "--kind", "gotcha", remembered]) == 0

    seen: list[str] = []

    def capture(instance, args, session_num, remain_on_exit=False, preamble=""):
        seen.append(preamble)
        instance.exit_file.write_text("0", encoding="utf-8")
        instance.stop_marker.touch()

    monkeypatch.setattr(op, "start_session", capture)
    op.run_loop_mode(op.Instance(SEAT), ["--agent", "test:agent"], is_fresh=True)
    assert seen, "the loop never launched a session, so this proves nothing"
    return seen[0]


def _commands(text: str) -> list[str]:
    spans = re.findall(r"`([^`]+)`", text)
    return sorted({s.strip() for s in spans
                   if s.strip().split()[:1] == ["operator"]})


def _run(template: str) -> int:
    argv = template.replace('\\"...\\"', "note").replace('"..."', "note")
    return entry.main(shlex.split(argv, posix=False)[1:])


def test_a_fresh_seat_is_told_how_to_remember(monkeypatch, tmp_path):
    found = _commands(_launch_preamble(monkeypatch, tmp_path))
    assert any("remember" in c for c in found), found
    for template in found:
        assert _run(template) != 2, f"the preamble advertises `{template}`"


def test_a_seat_with_memory_is_told_how_to_read_it(monkeypatch, tmp_path):
    """The read clause is conditional, so a launch with an empty journal never
    advertises recall and never exercises it."""
    found = _commands(_launch_preamble(monkeypatch, tmp_path,
                                       remembered="node 20 is required"))
    assert any("recall" in c for c in found), found
    for template in found:
        assert _run(template) != 2, f"the preamble advertises `{template}`"


def test_the_two_launches_between_them_cover_both_memory_commands(
        monkeypatch, tmp_path):
    """Neither launch alone does, which is how recall went unexecuted once."""
    fresh = _commands(_launch_preamble(monkeypatch, tmp_path))
    assert not any("recall" in c for c in fresh)


@pytest.mark.parametrize("verb", ["remember", "recall"])
def test_the_supervisor_still_passes_what_the_clause_needs(
        verb, monkeypatch, tmp_path):
    """`has_journal` is the supervisor's to compute and pass. Dropping that one
    argument would stop every real launch advertising recall, and a test that
    composed the preamble itself would not notice."""
    text = _launch_preamble(monkeypatch, tmp_path, remembered="something")
    assert any(verb in c for c in _commands(text))
