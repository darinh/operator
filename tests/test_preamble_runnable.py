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
    """Every backticked span that could be a command, whatever it names.

    The extractor must not know which program is the right one. This guard
    used to keep only spans whose first token was exactly `operator`, which
    made it structurally unable to report its own headline defect: the
    restart clause advertised `handoff`, a console script of `copilot-tools`,
    and the filter dropped it before any check ran. A test that finds its
    subject with the same rule it judges it by cannot report a subject that
    breaks the rule, so finding is permissive here and judging happens in
    `test_every_advertised_command_is_a_program_this_project_installs`.
    """
    found = set()
    for span in re.findall(r"`([^`]+)`", text):
        tokens = span.strip().split()
        if len(tokens) > 1 and re.fullmatch(r"[A-Za-z][A-Za-z0-9._-]*", tokens[0]):
            found.add(span.strip())
    return sorted(found)


def _installed_programs() -> frozenset:
    """The console scripts a machine gets by installing this project, alone.

    Read from the packaging metadata rather than a list retyped here, because
    the question being asked is exactly "would this command exist on a fresh
    devbox with nothing else on it". Asking the environment for `handoff`
    would have answered yes on the machine where the defect was found, since
    the predecessor tool was installed beside it.
    """
    from importlib import metadata
    dist = metadata.distribution("operator-kernel")
    return frozenset(entry.name for entry in dist.entry_points
                     if entry.group == "console_scripts")


def _run(template: str) -> int:
    argv = template.replace('\\"...\\"', "note").replace('"..."', "note")
    return entry.main(shlex.split(argv)[1:])


def test_every_advertised_command_is_a_program_this_project_installs(
        monkeypatch, tmp_path):
    """The check the old extractor could not perform.

    `handoff --instance ... --status ...` was advertised to every seat as the
    session-restart protocol while no distribution of this project installed
    a `handoff`. It resolved, on the machine where this was written, to a
    console script belonging to `copilot-tools`. On the fresh devbox this
    project is meant to stand alone on, the core loop's restart step simply
    did not exist.
    """
    ours = _installed_programs()
    for state in ({}, {"remembered": "node 20 is required"}):
        for template in _commands(_launch_preamble(monkeypatch, tmp_path, **state)):
            program = shlex.split(template)[0]
            assert program in ours, (
                f"the preamble advertises `{template}`, but {program!r} is "
                f"not one of this project's console scripts {sorted(ours)}")


def test_no_command_is_advertised_outside_backticks(monkeypatch, tmp_path):
    """Backticks are what makes a command visible to the extractor above.

    The `handoff` clause sat in bare prose, so every guard here read straight
    past it for as long as it existed. An option flag loose in the text is the
    signature: prose does not contain `--instance`, only a command does.
    """
    text = _launch_preamble(monkeypatch, tmp_path)
    prose = re.sub(r"`[^`]+`", " ", text)
    loose = re.findall(r"(?:^|\s)(--[A-Za-z][A-Za-z0-9-]*)", prose)
    assert not loose, (
        f"{loose} appears outside backticks, so a command is being advertised "
        f"where the extractor cannot see it")


def test_a_fresh_seat_is_told_how_to_restart_itself(monkeypatch, tmp_path):
    """Key fact (2) of every preamble, and the least tested thing in it."""
    found = _commands(_launch_preamble(monkeypatch, tmp_path))
    assert any("handoff" in c for c in found), found


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


@pytest.mark.parametrize("template", [
    'operator recall --instance alpha',
    'operator remember --instance alpha --kind gotcha "..."',
    'operator remember --instance alpha --kind "gotcha" "..."',
])
def test_a_quoted_option_value_is_not_mangled_into_a_false_failure(
        template, monkeypatch, tmp_path):
    """Positive control on the tokenizer.

    posix=False preserved the grouping quotes, so a clause writing --kind
    "gotcha" reached argparse as the literal '"gotcha"' and was rejected. The
    guard would have reported CLI incompatibility for a command the CLI
    accepts, which is the failure mode that gets a guard muted.
    """
    _launch_preamble(monkeypatch, tmp_path)
    assert _run(template) != 2, f"`{template}` is valid but the guard mangled it"
