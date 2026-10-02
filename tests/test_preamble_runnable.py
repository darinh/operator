"""Every command the supervisor advertises to an operator has to run.

The preamble is composed in one file and parsed in another, so the two can
drift while both suites stay green. What is captured here is the text a real
launch hands an operator, not a reconstruction of it: reconstructing the wiring
would keep passing if the supervisor stopped passing an argument.
"""
from __future__ import annotations

import re
import shlex

import op

from operator_cli import entry

OPERATOR = "alpha"

#: A single-token span is a command only if it could name a program. Multi-token
#: spans are always commands, whatever the first token is, which is what lets
#: `` `.\handoff.exe --instance x` `` be reported rather than mistaken for a
#: path. Reviewer A found three shapes slipping through the acceptance rule
#: this replaces, then a fourth that this exemption had to be narrowed for:
#: `` `handoff.exe` `` is an advertised program, `` `trace.jsonl` `` is a file.
_PATHLIKE = re.compile(r"[/\\.]")
_EXECUTABLE = (".exe", ".cmd", ".bat", ".ps1", ".sh", ".com")


def _launch_preamble(monkeypatch, tmp_path, *, remembered: str = "") -> str:
    """The text one real `run_loop_mode` session hands its operator."""
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

    from operator_cli.project import ensure_registered
    assert ensure_registered()[0] == 0
    import operators
    record = operators.create("alpha", work)

    seen: list[str] = []

    def capture(instance, args, session_num, remain_on_exit=False, preamble=""):
        seen.append(preamble)
        instance.exit_file.write_text("0", encoding="utf-8")
        instance.stop_marker.touch()

    monkeypatch.setattr(op, "start_session", capture)
    op.run_loop_mode(record.instance(), ["--agent", "test:agent"], is_fresh=True)
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
        span = span.strip()
        if not span:
            continue
        tokens = span.split()
        bare = tokens[0].strip("\"'")
        if (len(tokens) == 1 and _PATHLIKE.search(bare)
                and not bare.lower().endswith(_EXECUTABLE)):
            # `.operator/mandate.md` and `trace.jsonl` name files. A lone word
            # with no separator is a program, and so is one carrying an
            # executable suffix: `handoff.exe` advertises a program this
            # project does not install just as plainly as `handoff` does.
            # Quotes are stripped first, because `"handoff.exe"` is a command
            # spelling on Windows and not a different kind of thing.
            continue
        found.add(span)
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


def _launch_texts(monkeypatch, tmp_path):
    """Every preamble an operator can be handed, real launches first.

    The two real ones are what `run_loop_mode` actually produced, which is
    what this file exists to check. The rest are composed, because the clauses
    they carry are conditional on states a test cannot reach by launching --
    a stale wrapper, an unreadable handoff probe, a crash. Composing is a weak
    check for wiring and a sound one for reading the prose, which is all the
    caller does with these.
    """
    yield _launch_preamble(monkeypatch, tmp_path)
    import preamble as P
    from instance import Instance
    for extra in ({"crash_recovery": True}, {"handoff_unknown": True},
                  {"handoff_waiting": str(tmp_path / "h.md"),
                   "handoff_written": "2026-09-24T00:00:00Z"}):
        yield P.build_preamble(Instance(OPERATOR), **extra)


def _every_advertised_command(monkeypatch, tmp_path):
    for text in _launch_texts(monkeypatch, tmp_path):
        yield from _commands(text)


def test_every_advertised_command_is_a_program_this_project_installs(
        monkeypatch, tmp_path):
    """The check the old extractor could not perform.

    `handoff --instance ... --status ...` was advertised to every operator as the
    session-restart protocol while no distribution of this project installed
    a `handoff`. It resolved, on the machine where this was written, to a
    console script belonging to `copilot-tools`. On the fresh devbox this
    project is meant to stand alone on, the core loop's restart step simply
    did not exist.
    """
    ours = _installed_programs()
    seen = 0
    for template in _every_advertised_command(monkeypatch, tmp_path):
        program = shlex.split(template)[0]
        seen += 1
        assert program in ours, (
            f"the preamble advertises `{template}`, but {program!r} is "
            f"not one of this project's console scripts {sorted(ours)}")
    assert seen, "no commands were extracted, so this proved nothing"


def test_every_advertised_operator_verb_is_one_the_cli_dispatches(
        monkeypatch, tmp_path):
    """Checking the executable is not checking the command.

    The test above asks only whether `shlex.split(template)[0]` is one of our
    console scripts, so `operator listt` passes it: the typo is in the verb,
    and nothing reads the verb. Dispatchability is what can be checked
    without side effects.
    """
    seen = 0
    for template in _every_advertised_command(monkeypatch, tmp_path):
        parts = shlex.split(template)
        if parts[0] != "operator":
            continue
        assert len(parts) > 1, f"`{template}` names no verb at all"
        seen += 1
        assert parts[1] in entry.HANDLERS, (
            f"the preamble advertises `{template}`, but {parts[1]!r} is not "
            f"a verb `operator` dispatches {sorted(entry.HANDLERS)}")
    assert seen, "no operator commands were extracted, so this proved nothing"


def test_no_command_is_advertised_outside_backticks(monkeypatch, tmp_path):
    """Backticks are what makes a command visible to the extractor above.

    The `handoff` clause sat in bare prose, so every guard here read straight
    past it for as long as it existed. Two signatures give a loose command
    away: an option flag, and one of our programs followed by one of our
    verbs. The second is narrow on purpose. "the operator supervisor" is an
    ordinary English phrase in this text, so any looser rule fails on prose
    that is doing nothing wrong.
    """
    for text in _launch_texts(monkeypatch, tmp_path):
        prose = re.sub(r"`[^`]+`", " ", text)
        loose = re.findall(r"(?:^|\s)(--[A-Za-z][A-Za-z0-9-]*)", prose)
        assert not loose, (
            f"{loose} appears outside backticks, so a command is being "
            f"advertised where the extractor cannot see it")
        for program in _installed_programs():
            for verb in entry.HANDLERS:
                assert not re.search(
                    rf"(?:^|\s){re.escape(program)}\s+{re.escape(verb)}\b",
                    prose), (
                    f"`{program} {verb}` appears outside backticks, which is "
                    f"how the handoff clause hid from every guard here")


def test_a_fresh_operator_is_told_how_to_restart_itself(monkeypatch, tmp_path):
    """Key fact (2) of every preamble, and the least tested thing in it.

    Naming it is not enough, which is the whole lesson of this file: the
    command is run, so a clause that drifts from the parser fails here.
    """
    from instance import restart_marker_for
    from paths import project_handoff_file

    found = _commands(_launch_preamble(monkeypatch, tmp_path))
    restart = [c for c in found if "handoff" in c]
    assert len(restart) == 1, found
    assert "--instance" not in restart[0]
    import json
    import operators
    import process_identity
    import process_tree
    record = operators.find("alpha")
    assert record is not None
    op_id = record.id
    handoff = project_handoff_file(tmp_path / "work", op_id)
    marker = restart_marker_for(op_id)
    assert not handoff.exists() and not marker.exists()
    # The advertised command no longer names an operator. Identity is the
    # process tree, so the guard has to seat this process in alpha's custody
    # or the command it runs is one a real session could not run either.
    pid, token, session = 424242, "win:100", 1
    op.Instance(op_id).custody_file.write_text(
        json.dumps({"pid": pid, "start": token, "session": session}),
        encoding="utf-8")
    monkeypatch.setattr(process_tree, "ancestry", lambda _pid: [pid])
    monkeypatch.setattr(process_identity, "process_start_token",
                        lambda asked: token if asked == pid else None)
    assert _run(restart[0]) == 0, f"the preamble advertises `{restart[0]}`"
    assert handoff.read_text(encoding="utf-8").strip()
    assert json.loads(marker.read_text(encoding="utf-8")) == {
        "id": op_id, "session": session}


def test_the_restart_clause_does_not_name_an_operator():
    """Identity is derived from the process tree. A clause that names a
    display name, or even an id, is a handoff for whoever typed it."""
    import preamble as P
    from instance import Instance

    operator = Instance("op-abcdef01", "a.b")
    assert operator.id != operator.display_name
    restart = [c for c in _commands(P.build_preamble(operator)) if "handoff" in c]
    assert restart, "no handoff command was advertised at all"
    for command in restart:
        assert "--instance" not in command, command
        assert operator.id not in command, command
        assert operator.display_name not in command, command


def test_an_executable_named_in_a_lone_span_is_still_a_command():
    """Reviewer A's fourth shape. `handoff.exe` advertises a program this
    project does not install just as plainly as `handoff` does, and the
    path-like exemption was swallowing it along with `trace.jsonl`."""
    for span in ("handoff", "handoff.exe", ".\\handoff.exe", "git.exe",
                 "operator handoff --instance a"):
        assert _commands(f"text `{span}` more"), span
    for span in (".operator/mandate.md", "trace.jsonl", "extensions.json"):
        assert not _commands(f"text `{span}` more"), span


def test_a_quoted_executable_is_still_a_command():
    """`"handoff.exe"` is a command spelling on Windows, and the suffix test
    was reading the closing quote as part of the extension."""
    for span in ('"handoff.exe"', "'handoff.exe'", '".\\handoff.exe"',
                 "handoff.EXE"):
        assert _commands(f"run `{span}` now"), span
