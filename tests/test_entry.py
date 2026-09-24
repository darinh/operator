"""The `operator` front door: a menu you can read, verbs you can type.

The menu is the risky half because it reads stdin. These tests never attach a
real terminal. They feed a scripted stdin, or they prove stdin is not read.
"""
from __future__ import annotations

import io
import json
import os
import sys
from pathlib import Path

import op
from operator_cli import entry as cli

REPO = Path(__file__).resolve().parent.parent


class FakeTTY(io.StringIO):
    def isatty(self):
        return True


class Boom:
    def isatty(self):
        return False

    def readline(self, *a, **k):
        raise AssertionError("read stdin without a TTY")

    def read(self, *a, **k):
        raise AssertionError("read stdin without a TTY")

    def __iter__(self):
        raise AssertionError("read stdin without a TTY")


def _tty(monkeypatch, text=""):
    monkeypatch.setattr(sys, "stdin", FakeTTY(text))
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)


def _choice_for(argv):
    for index, item in enumerate(cli.menu_items(), 1):
        if item.argv == argv:
            return index
    raise AssertionError(f"no menu entry for {argv}")


# ── help and the TTY gate ───────────────────────────────────────


def test_help_lists_every_verb(capsys):
    assert cli.main(["--help"]) == 0
    out = capsys.readouterr().out
    for verb in cli.VERBS:
        assert " ".join(verb.tokens) in out, verb.tokens


def test_help_after_home_is_still_help(capsys):
    assert cli.main(["--home", "/somewhere", "--help"]) == 0
    out = capsys.readouterr().out
    assert "unknown command" not in out
    assert "start" in out


def test_help_does_not_read_stdin(monkeypatch, capsys):
    monkeypatch.setattr(sys, "stdin", Boom())
    assert cli.main(["--help"]) == 0
    assert "start" in capsys.readouterr().out


def test_no_tty_prints_help_and_exits_nonzero(monkeypatch, capsys):
    """CI runs `operator` with no TTY. A menu that blocked would be a build break.

    stdin raises if read, so removing the TTY check fails this test rather than
    hanging the suite.
    """
    monkeypatch.setattr(sys, "stdin", Boom())
    monkeypatch.setattr(sys.stdout, "isatty", lambda: False)
    rc = cli.main([])
    assert rc != 0
    err = capsys.readouterr().err
    assert "Usage: operator" in err
    for verb in cli.VERBS:
        assert " ".join(verb.tokens) in err, verb.tokens


def test_absent_stdin_prints_help(monkeypatch, capsys):
    monkeypatch.setattr(sys, "stdin", None)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
    rc = cli.main([])
    assert rc != 0
    assert "Usage: operator" in capsys.readouterr().err


def test_closed_stdin_prints_help(monkeypatch, capsys):
    closed = io.StringIO()
    closed.close()
    monkeypatch.setattr(sys, "stdin", closed)
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
    rc = cli.main([])
    assert rc != 0
    assert "Usage: operator" in capsys.readouterr().err


def test_a_pipe_on_stdin_does_not_open_the_menu(monkeypatch, capsys):
    monkeypatch.setattr(sys, "stdin", Boom())
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
    rc = cli.main([])
    assert rc != 0
    assert "Usage: operator" in capsys.readouterr().err


def test_unknown_command_prints_help(capsys):
    assert cli.main(["frobnicate"]) == 2
    err = capsys.readouterr().err
    assert "unknown command: frobnicate" in err
    assert "start" in err


# ── menu ────────────────────────────────────────────────────────


def test_the_verb_table_is_not_empty():
    """Otherwise the two loops below pass over nothing."""
    names = {verb.tokens[0] for verb in cli.VERBS}
    assert len(cli.VERBS) >= 10
    assert names >= {"doctor", "start", "list", "join", "stop", "trace", "verify"}


def test_every_menu_entry_maps_to_a_verb():
    for item in cli.menu_items():
        assert any(item.argv[:len(verb.tokens)] == verb.tokens
                   for verb in cli.VERBS), item


def test_every_verb_has_a_handler():
    for verb in cli.VERBS:
        assert verb.tokens[0] in cli.HANDLERS, verb.tokens


def test_menu_dispatches_to_list(monkeypatch, capsys):
    import supervisor_control
    monkeypatch.setattr(supervisor_control, "active_instances",
                        lambda: [op.Instance("alpha")])
    _tty(monkeypatch, f"{_choice_for(('list',))}\n")
    assert cli.main([]) == 0
    out = capsys.readouterr().out
    assert "Running: operator list" in out
    assert "alpha" in out
    assert "Quit" in out


def test_menu_prompts_then_joins(monkeypatch, capsys):
    seen = []
    monkeypatch.setattr(op.MUX, "has_session", lambda session: True)
    monkeypatch.setattr(op.MUX, "attach", lambda session: seen.append(session) or 0)
    _tty(monkeypatch, f"{_choice_for(('join',))}\nalpha\n")
    assert cli.main([]) == 0
    assert seen == ["alpha"]
    assert "Running: operator join alpha" in capsys.readouterr().out


def _split_printed(command: str) -> list[str]:
    if os.name != "nt":
        import shlex
        return shlex.split(command)
    out, i, n = [], 0, len(command)
    while i < n:
        if command[i].isspace():
            i += 1
            continue
        if command[i] == "'":
            i += 1
            buf = []
            while i < n:
                if command[i] == "'" and i + 1 < n and command[i + 1] == "'":
                    buf.append("'")
                    i += 2
                    continue
                if command[i] == "'":
                    i += 1
                    break
                buf.append(command[i])
                i += 1
            out.append("".join(buf))
            continue
        j = i
        while j < n and not command[j].isspace():
            j += 1
        out.append(command[i:j])
        i = j
    return out


def test_menu_quotes_a_name_with_spaces(monkeypatch, capsys):
    seen = []
    monkeypatch.setattr(op.MUX, "has_session", lambda session: True)
    monkeypatch.setattr(op.MUX, "attach", lambda session: seen.append(session) or 0)
    _tty(monkeypatch, f"{_choice_for(('join',))}\nalpha beta\n")
    assert cli.main([]) == 0
    assert seen == ["alpha beta"]
    out = capsys.readouterr().out
    line = [row for row in out.splitlines() if "Running:" in row]
    assert line, out
    assert _split_printed(line[0].split("operator ", 1)[1]) == ["join", "alpha beta"]


def test_printed_command_round_trips_a_quote_and_a_dollar():
    argv = ["remember", "--kind", "gotcha", "can't look at $HOME"]
    quoted = cli._argv.quote_argv(argv)
    assert _split_printed(quoted) == argv
    assert "$HOME" in quoted
    if os.name == "nt":
        assert "can''t look at $HOME" in quoted


def test_menu_offers_recover_all():
    assert any(item.argv == ("recover", "--all") for item in cli.menu_items())


def test_menu_offers_project_verbs():
    items = {item.argv for item in cli.menu_items()}
    assert ("project", "register") in items
    assert ("project", "list") in items
    assert ("project", "forget") in items


def test_menu_offers_ext_verbs():
    items = {item.argv for item in cli.menu_items()}
    assert ("ext", "list") in items
    assert ("ext", "enable") in items
    assert ("ext", "disable") in items


def test_doctor_is_first_on_the_menu():
    assert cli.menu_items()[0].argv == ("doctor",)


def test_menu_start_does_not_inject_an_agent(monkeypatch, capsys):
    import supervisor
    seen = {}

    def fake(instance, copilot_args, is_fresh, adopt=False, cwd=None):
        seen.update(name=instance.display_name, args=list(copilot_args))
        return 9

    monkeypatch.setattr(supervisor, "_spawn_background_loop", fake)
    _tty(monkeypatch, f"{_choice_for(('start',))}\ndemo\nfix the parser\n\nn\n")
    assert cli.main([]) == 0
    assert seen["name"] == "demo"
    assert "--agent" not in seen["args"]
    assert "anvil:anvil" not in seen["args"]
    assert "fix the parser" in seen["args"]
    out = capsys.readouterr().out
    assert "Running: operator start --name demo" in out
    assert "fix the parser" in out


def test_menu_start_passes_an_agent_and_can_attach(monkeypatch):
    import supervisor
    spawned = {}
    attached = []

    def fake(instance, copilot_args, is_fresh, adopt=False, cwd=None):
        spawned.update(name=instance.display_name, args=list(copilot_args))
        return 3

    monkeypatch.setattr(supervisor, "_spawn_background_loop", fake)
    monkeypatch.setattr(op.MUX, "has_session", lambda session: True)
    monkeypatch.setattr(op.MUX, "attach", lambda session: attached.append(session) or 0)
    _tty(monkeypatch, f"{_choice_for(('start',))}\ndemo\n\nmy-agent\ny\n")
    assert cli.main([]) == 0
    assert spawned["args"] == ["--agent", "my-agent"]
    assert attached == ["demo"]


def test_menu_reprompts_for_a_missing_seat_name(monkeypatch, capsys):
    seen = []
    monkeypatch.setattr(op.MUX, "has_session", lambda session: True)
    monkeypatch.setattr(op.MUX, "attach", lambda session: seen.append(session) or 0)
    _tty(monkeypatch, f"{_choice_for(('join',))}\n\nalpha\n")
    assert cli.main([]) == 0
    assert seen == ["alpha"]
    assert "A seat name is needed." in capsys.readouterr().out


def test_menu_quit_does_not_dispatch(monkeypatch, capsys):
    _tty(monkeypatch, "0\n")
    assert cli.main([]) == 0
    out = capsys.readouterr().out
    assert "Running:" not in out
    assert "Quit" in out


def test_a_menu_started_seat_exports_the_home_its_child_reads(monkeypatch):
    """A seat chosen from the menu must be as defended as a typed one.

    `_settle_home` exports unconditionally so that parent and child read the
    same string instead of independently agreeing on a default. Reaching a verb
    through the menu used to skip it, so the agreement was a coincidence of
    both sides resolving `Path.home()` the same way.
    """
    import supervisor
    seen = {}

    def fake(instance, copilot_args, is_fresh, adopt=False, cwd=None):
        seen["home"] = os.environ.get("COPILOT_OPERATOR_HOME")
        return 7

    monkeypatch.setattr(supervisor, "_spawn_background_loop", fake)
    monkeypatch.delenv("COPILOT_OPERATOR_HOME", raising=False)
    _tty(monkeypatch, f"{_choice_for(('start',))}\nalpha\n\n\nn\n")
    assert cli.main([]) == 0
    assert seen["home"] == str(Path.home() / ".operator")


def test_dispatch_settles_the_home_for_every_caller(monkeypatch):
    """The guard for the defect, at the seam rather than at one caller.

    Both entry points reach a verb through `dispatch`, so settling there is
    what makes the export unskippable. A third caller added later inherits it.
    """
    import supervisor_control
    monkeypatch.setattr(supervisor_control, "active_instances", lambda: [])
    monkeypatch.delenv("COPILOT_OPERATOR_HOME", raising=False)
    assert cli.dispatch(["list"]) == 0
    assert os.environ["COPILOT_OPERATOR_HOME"] == str(Path.home() / ".operator")


def test_a_typed_home_still_reaches_the_child(monkeypatch, tmp_path):
    import supervisor
    seen = {}

    def fake(instance, copilot_args, is_fresh, adopt=False, cwd=None):
        seen["home"] = os.environ.get("COPILOT_OPERATOR_HOME")
        return 7

    monkeypatch.setattr(supervisor, "_spawn_background_loop", fake)
    assert cli.main(["--home", str(tmp_path), "start", "--name", "alpha"]) == 0
    assert seen["home"] == str(tmp_path)


# ── verbs ───────────────────────────────────────────────────────


def test_start_spawns_the_background_supervisor(monkeypatch, capsys):
    import supervisor
    seen = {}

    def fake(instance, copilot_args, is_fresh, adopt=False, cwd=None):
        seen.update(name=instance.display_name, args=list(copilot_args),
                    fresh=is_fresh)
        return 4242

    monkeypatch.setattr(supervisor, "_spawn_background_loop", fake)
    assert cli.main(["start", "--name", "alpha", "--agent", "test:agent"]) == 0
    assert seen == {"name": "alpha", "args": ["--agent", "test:agent"],
                    "fresh": False}
    assert "started alpha (pid 4242)" in capsys.readouterr().out


def test_start_accepts_a_positional_name(monkeypatch, capsys):
    import supervisor
    seen = {}

    def fake(instance, copilot_args, is_fresh, adopt=False, cwd=None):
        seen.update(name=instance.display_name, args=list(copilot_args))
        return 1

    monkeypatch.setattr(supervisor, "_spawn_background_loop", fake)
    assert cli.main(["start", "alpha", "--agent", "test:agent"]) == 0
    assert seen == {"name": "alpha", "args": ["--agent", "test:agent"]}


def test_start_keeps_the_parent_name_past_the_terminator(monkeypatch):
    import supervisor
    seen = {}

    def fake(instance, copilot_args, is_fresh, adopt=False, cwd=None):
        seen.update(name=instance.display_name, args=list(copilot_args))
        return 1

    monkeypatch.setattr(supervisor, "_spawn_background_loop", fake)
    assert cli.main([
        "start", "--name", "alpha", "--", "--name=beta", "some text",
    ]) == 0
    assert seen["name"] == "alpha"
    assert seen["args"] == ["--", "--name=beta", "some text"]


def test_start_without_a_name_refuses(capsys):
    assert cli.main(["start"]) == 2
    assert "Usage: operator start" in capsys.readouterr().err


def test_list_prints_running_seats(monkeypatch, capsys):
    import supervisor_control
    monkeypatch.setattr(supervisor_control, "active_instances",
                        lambda: [op.Instance("alpha"), op.Instance("bravo")])
    assert cli.main(["list"]) == 0
    out = capsys.readouterr().out
    assert "alpha" in out and "bravo" in out


def test_list_says_so_when_nothing_is_running(monkeypatch, capsys):
    import supervisor_control
    monkeypatch.setattr(supervisor_control, "active_instances", lambda: [])
    assert cli.main(["list"]) == 0
    assert "No running seats." in capsys.readouterr().out


def test_join_attaches(monkeypatch):
    seen = []
    monkeypatch.setattr(op.MUX, "has_session", lambda session: True)
    monkeypatch.setattr(op.MUX, "attach", lambda session: seen.append(session) or 7)
    assert cli.main(["join", "alpha"]) == 7
    assert seen == ["alpha"]


def test_join_without_a_multiplexer_explains(monkeypatch, capsys):
    from mux import MuxNotFoundError

    def boom(*a, **k):
        raise MuxNotFoundError(
            "No terminal multiplexer found. Install psmux:\n"
            "    winget install --id marlocarlo.psmux")

    monkeypatch.setattr(op.MUX, "has_session", boom)
    assert cli.main(["join", "alpha"]) == 1
    err = capsys.readouterr().err
    assert "Traceback" not in err
    assert "multiplexer" in err.lower()
    assert "Install" in err


def test_join_without_a_name_refuses(capsys):
    assert cli.main(["join"]) == 2
    assert "Usage: operator join NAME" in capsys.readouterr().err


def test_stop_requests_the_supervisor_stop(monkeypatch, capsys):
    import supervisor_control
    seen = []
    inst = op.Instance("alpha")
    inst.managed_file.parent.mkdir(parents=True, exist_ok=True)
    inst.managed_file.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(supervisor_control, "_request_supervisor_stop",
                        lambda inst: seen.append(inst.display_name))
    assert cli.main(["stop", "alpha"]) == 0
    assert seen == ["alpha"]
    assert "stop requested for alpha" in capsys.readouterr().out


def test_stop_unknown_refuses(monkeypatch, capsys):
    import supervisor_control
    called = []
    monkeypatch.setattr(supervisor_control, "_request_supervisor_stop",
                        lambda inst: called.append(inst.display_name))
    assert cli.main(["stop", "nonexistent"]) == 1
    assert called == []
    err = capsys.readouterr().err
    assert "No seat 'nonexistent'" in err


def test_doctor_reports_a_missing_copilot(monkeypatch, capsys):
    monkeypatch.setattr(cli.shutil, "which", lambda name: None)
    assert cli.main(["doctor"]) == 1
    out = capsys.readouterr().out
    assert "copilot: not found on PATH" in out
    assert "doctor: failed" in out


def test_doctor_ok_when_the_machine_is_ready(monkeypatch, capsys):
    monkeypatch.setattr(cli.shutil, "which", lambda name: "/bin/copilot")
    assert cli.main(["doctor"]) == 0
    out = capsys.readouterr().out
    assert "copilot: /bin/copilot" in out
    assert "doctor: ok" in out


def test_restart_loop_names_one_seat(monkeypatch):
    import supervisor_control
    seen = []
    monkeypatch.setattr(supervisor_control, "restart_loop",
                        lambda target: seen.append(target) or 0)
    assert cli.main(["restart-loop", "alpha"]) == 0
    assert seen == ["alpha"]


def test_restart_loop_all_after_terminator_is_not_a_sweep(monkeypatch):
    import supervisor_control
    swept = []
    named = []
    monkeypatch.setattr(supervisor_control, "restart_all_loops",
                        lambda: swept.append(True) or 0)
    monkeypatch.setattr(supervisor_control, "restart_loop",
                        lambda target: named.append(target) or 0)
    assert cli.main(["restart-loop", "--", "--all"]) == 0
    assert swept == []
    assert named == ["--all"]


def test_restart_loop_all_sweeps(monkeypatch):
    import supervisor_control
    called = []
    monkeypatch.setattr(supervisor_control, "restart_all_loops",
                        lambda: called.append("--all") or 0)
    monkeypatch.setattr(supervisor_control, "restart_loop",
                        lambda target: (_ for _ in ()).throw(
                            AssertionError("single restart")))
    assert cli.main(["restart-loop", "--all"]) == 0
    assert called == ["--all"]


def test_recover_delegates(monkeypatch):
    seen = []
    monkeypatch.setattr(cli.recover, "main", lambda argv: seen.append(argv) or 0)
    assert cli.main(["recover", "--all"]) == 0
    assert seen == [["--all"]]


def test_remember_stops_options_at_the_terminator(monkeypatch):
    seen = []
    monkeypatch.setattr(cli.seat, "main", lambda argv: seen.append(list(argv)) or 0)
    assert cli.main([
        "remember", "--instance", "alpha", "--kind", "decision",
        "--", "--instance=beta", "some text",
    ]) == 0
    assert seen == [[
        "--instance", "alpha", "remember", "--kind", "decision",
        "--", "--instance=beta", "some text",
    ]]


def test_session_after_terminator_is_journal_text(monkeypatch):
    seen = []
    monkeypatch.setattr(cli.seat, "main", lambda argv: seen.append(list(argv)) or 0)
    assert cli.main([
        "remember", "--instance", "alpha", "--kind", "decision",
        "--", "--session=99", "note",
    ]) == 0
    assert seen[0][-3:] == ["--", "--session=99", "note"]
    assert seen[0][0:2] == ["--instance", "alpha"]


def test_home_after_terminator_is_journal_text(monkeypatch, tmp_path):
    seen = []
    monkeypatch.setattr(cli.seat, "main", lambda argv: seen.append(list(argv)) or 0)
    assert cli.main([
        "--home", str(tmp_path),
        "remember", "--instance", "alpha", "--kind", "decision",
        "--", "--home=/elsewhere", "note",
    ]) == 0
    assert "--home=/elsewhere" in seen[0]
    assert seen[0][-1] == "note"
    assert os.environ["COPILOT_OPERATOR_HOME"] == str(tmp_path)


def test_remember_delegates_to_seat(monkeypatch):
    seen = []
    monkeypatch.setattr(cli.seat, "main", lambda argv: seen.append(argv) or 0)
    assert cli.main(["remember", "--instance", "prism", "--kind", "gotcha",
                     "tail by inode"]) == 0
    assert seen == [["--instance", "prism", "remember", "--kind", "gotcha",
                     "tail by inode"]]


def test_recall_delegates_to_seat(monkeypatch):
    seen = []
    monkeypatch.setattr(cli.seat, "main", lambda argv: seen.append(argv) or 0)
    assert cli.main(["recall", "--instance", "prism"]) == 0
    assert seen == [["--instance", "prism", "recall"]]


def test_forget_delegates_to_seat(monkeypatch):
    seen = []
    monkeypatch.setattr(cli.seat, "main", lambda argv: seen.append(argv) or 0)
    assert cli.main(["forget", "--instance", "prism", "abc"]) == 0
    assert seen == [["--instance", "prism", "forget", "abc"]]


def test_fleet_run_delegates(monkeypatch):
    seen = []
    monkeypatch.setattr(cli.fleet, "main", lambda argv: seen.append(argv) or 0)
    assert cli.main(["fleet", "run", "--rounds", "1"]) == 0
    assert seen == [["run", "--rounds", "1"]]


def test_fleet_proposals_delegates(monkeypatch):
    seen = []
    monkeypatch.setattr(cli.fleet, "main", lambda argv: seen.append(argv) or 0)
    assert cli.main(["fleet", "proposals", "--drain"]) == 0
    assert seen == [["proposals", "--drain"]]


def test_fleet_without_a_subcommand_refuses(capsys):
    assert cli.main(["fleet"]) == 2
    assert "operator fleet run|proposals" in capsys.readouterr().err


def test_trace_prints_newest_first_with_a_limit(tmp_path, capsys):
    path = tmp_path / "trace.jsonl"
    path.write_text(
        json.dumps({"n": 1}) + "\n"
        + json.dumps({"n": 2}) + "\n"
        + json.dumps({"n": 3}) + "\n",
        encoding="utf-8")
    assert cli.main(["--home", str(tmp_path), "trace", "-n", "2"]) == 0
    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines()
             if line.strip()]
    assert [row["n"] for row in lines] == [3, 2]


def test_trace_says_so_when_the_ledger_is_missing(tmp_path, capsys):
    assert cli.main(["--home", str(tmp_path), "trace"]) == 0
    assert "no ledger" in capsys.readouterr().out


def test_trace_reads_the_rotated_half_when_the_live_file_is_empty(tmp_path,
                                                                 capsys):
    """The case where the old reader returned success with no output at all.

    `trace.jsonl` rotates by rename, so every record can be in `trace.jsonl.1`
    with nothing written since. The guard already knew that file could hold
    records; the reader ignored it and printed nothing, which reads as an
    empty ledger rather than as a reader that did not look.
    """
    (tmp_path / "trace.jsonl.1").write_text(
        json.dumps({"n": 1}) + "\n"
        + json.dumps({"n": 2}) + "\n"
        + json.dumps({"n": 3}) + "\n",
        encoding="utf-8")
    (tmp_path / "trace.jsonl").write_text("", encoding="utf-8")
    assert cli.main(["--home", str(tmp_path), "trace"]) == 0
    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines()
             if line.strip()]
    assert [row["n"] for row in lines] == [3, 2, 1]


def test_trace_spans_the_rotation_oldest_in_the_rotated_file(tmp_path, capsys):
    (tmp_path / "trace.jsonl.1").write_text(
        json.dumps({"n": 1}) + "\n" + json.dumps({"n": 2}) + "\n",
        encoding="utf-8")
    (tmp_path / "trace.jsonl").write_text(
        json.dumps({"n": 3}) + "\n" + json.dumps({"n": 4}) + "\n",
        encoding="utf-8")
    assert cli.main(["--home", str(tmp_path), "trace"]) == 0
    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines()
             if line.strip()]
    assert [row["n"] for row in lines] == [4, 3, 2, 1]


def test_trace_and_verify_count_the_same_records(tmp_path, capsys):
    """`verify` reading five while `trace` shows two is the reportable shape."""
    import ledger_chain
    writer = ledger_chain.Writer("w1")
    (tmp_path / "trace.jsonl.1").write_text(
        "".join(json.dumps(writer.stamp({"event": str(i)})) + "\n"
                for i in range(3)),
        encoding="utf-8")
    (tmp_path / "trace.jsonl").write_text(
        "".join(json.dumps(writer.stamp({"event": str(i)})) + "\n"
                for i in range(3, 5)),
        encoding="utf-8")
    assert cli.main(["--home", str(tmp_path), "trace", "-n", "100"]) == 0
    shown = len([line for line in capsys.readouterr().out.splitlines()
                 if line.strip()])
    assert cli.main(["--home", str(tmp_path), "verify"]) == 0
    assert "5 record(s)" in capsys.readouterr().out
    assert shown == 5


def test_verify_prints_a_verified_chain(tmp_path, capsys):
    import ledger_chain
    writer = ledger_chain.Writer("w1")
    path = tmp_path / "trace.jsonl"
    path.write_text(
        json.dumps(writer.stamp({"event": "one"})) + "\n"
        + json.dumps(writer.stamp({"event": "two"})) + "\n",
        encoding="utf-8")
    assert cli.main(["--home", str(tmp_path), "verify"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("verified:")
    assert "2 record(s)" in out
    assert "1 writer(s)" in out


def test_verify_prints_a_gap(tmp_path, capsys):
    import ledger_chain
    writer = ledger_chain.Writer("alice")
    first = writer.stamp({"event": "one"})
    writer.stamp({"event": "two"})
    third = writer.stamp({"event": "three"})
    path = tmp_path / "trace.jsonl"
    path.write_text(json.dumps(first) + "\n" + json.dumps(third) + "\n",
                    encoding="utf-8")
    assert cli.main(["--home", str(tmp_path), "verify"]) == 1
    out = capsys.readouterr().out
    assert out.startswith("gap:")
    assert "alice" in out


def test_the_console_script_is_declared():
    text = (REPO / "pyproject.toml").read_text(encoding="utf-8")
    assert 'operator = "operator_cli.entry:main"' in text
    assert "operator-fleet" in text
    assert "operator-seat" in text
    assert "operator-recover" in text
