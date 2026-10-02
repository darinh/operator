"""The `operator` front door: a menu you can read, verbs you can type.

The menu is the risky half because it reads stdin. These tests never attach a
real terminal. They feed a scripted stdin, or they prove stdin is not read.
"""
from __future__ import annotations

import io
import os
import sys
from pathlib import Path

import pytest

import op
import paths
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


@pytest.fixture(autouse=True)
def _launch_ready(monkeypatch):
    import supervisor_control
    monkeypatch.setattr(supervisor_control, "launch_status",
                        lambda inst, pid, **k: ("ready", pid))


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
    assert len(cli.VERBS) >= 7
    assert names >= {"doctor", "start", "list", "attach", "stop", "rename",
                     "delete", "recover", "handoff"}


def test_every_menu_entry_maps_to_a_verb():
    for item in cli.menu_items():
        assert any(item.argv[:len(verb.tokens)] == verb.tokens
                   for verb in cli.VERBS), item


def test_every_verb_has_a_handler():
    for verb in cli.VERBS:
        assert verb.tokens[0] in cli.HANDLERS, verb.tokens


def test_menu_dispatches_to_list(monkeypatch, capsys):
    import operators
    import supervisor_control
    record = operators.create("alpha", Path.cwd())
    monkeypatch.setattr(supervisor_control, "active_instances",
                        lambda: [record.instance()])
    _tty(monkeypatch, f"{_choice_for(('list',))}\n")
    assert cli.main([]) == 0
    out = capsys.readouterr().out
    assert "Running: operator list" in out
    assert "alpha" in out
    assert "Quit" in out


def test_menu_prompts_then_attaches(monkeypatch, capsys):
    seen = []
    monkeypatch.setattr(op.MUX, "has_session", lambda session: True)
    monkeypatch.setattr(op.MUX, "attach", lambda session: seen.append(session) or 0)
    import operators
    record = operators.create("alpha", Path.cwd())
    _tty(monkeypatch, f"{_choice_for(('attach',))}\nalpha\n")
    assert cli.main([]) == 0
    assert seen == [record.id]
    assert "Running: operator attach alpha" in capsys.readouterr().out


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
    import operators
    record = operators.create("alpha beta", Path.cwd())
    _tty(monkeypatch, f"{_choice_for(('attach',))}\nalpha beta\n")
    assert cli.main([]) == 0
    assert seen == [record.id]
    out = capsys.readouterr().out
    line = [row for row in out.splitlines() if "Running:" in row]
    assert line, out
    assert _split_printed(line[0].split("operator ", 1)[1]) == ["attach", "alpha beta"]


def test_printed_command_round_trips_a_quote_and_a_dollar():
    argv = ["remember", "--kind", "gotcha", "can't look at $HOME"]
    quoted = cli._argv.quote_argv(argv)
    assert _split_printed(quoted) == argv
    assert "$HOME" in quoted
    if os.name == "nt":
        assert "can''t look at $HOME" in quoted


def test_menu_offers_recover_all():
    assert any(item.argv == ("recover", "--all") for item in cli.menu_items())


def test_doctor_is_first_on_the_menu():
    assert cli.menu_items()[0].argv == ("doctor",)


def test_menu_start_does_not_inject_an_agent(monkeypatch, capsys):
    import supervisor
    seen = {}

    def fake(instance, copilot_args, is_fresh, cwd=None):
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

    def fake(instance, copilot_args, is_fresh, cwd=None):
        spawned.update(name=instance.display_name, args=list(copilot_args))
        return 3

    monkeypatch.setattr(supervisor, "_spawn_background_loop", fake)
    monkeypatch.setattr(op.MUX, "has_session", lambda session: True)
    monkeypatch.setattr(op.MUX, "attach", lambda session: attached.append(session) or 0)
    _tty(monkeypatch, f"{_choice_for(('start',))}\ndemo\n\nmy-agent\ny\n")
    assert cli.main([]) == 0
    assert spawned["args"] == ["--agent", "my-agent"]
    assert attached and attached[0].startswith("op-")


def test_menu_reprompts_for_a_missing_operator_name(monkeypatch, capsys):
    import operators
    record = operators.create("alpha", Path.cwd())
    seen = []
    monkeypatch.setattr(op.MUX, "has_session", lambda session: True)
    monkeypatch.setattr(op.MUX, "attach", lambda session: seen.append(session) or 0)
    _tty(monkeypatch, f"{_choice_for(('attach',))}\n\nalpha\n")
    assert cli.main([]) == 0
    assert seen == [record.id]
    assert "An operator name is needed." in capsys.readouterr().out


def test_menu_quit_does_not_dispatch(monkeypatch, capsys):
    _tty(monkeypatch, "0\n")
    assert cli.main([]) == 0
    out = capsys.readouterr().out
    assert "Running:" not in out
    assert "Quit" in out


def test_a_menu_started_operator_exports_the_home_its_child_reads(monkeypatch):
    """An operator chosen from the menu must be as defended as a typed one.

    `_settle_home` exports unconditionally so that parent and child read the
    same string instead of independently agreeing on a default. Reaching a verb
    through the menu used to skip it, so the agreement was a coincidence of
    both sides resolving `Path.home()` the same way.
    """
    import supervisor
    seen = {}

    def fake(instance, copilot_args, is_fresh, cwd=None):
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

    def fake(instance, copilot_args, is_fresh, cwd=None):
        seen["home"] = os.environ.get("COPILOT_OPERATOR_HOME")
        return 7

    monkeypatch.setattr(supervisor, "_spawn_background_loop", fake)
    assert cli.main(["--home", str(tmp_path), "start", "--name", "alpha"]) == 0
    assert seen["home"] == str(tmp_path)


# ── verbs ───────────────────────────────────────────────────────


def test_start_registers_an_unregistered_directory(tmp_path, monkeypatch, capsys):
    import supervisor
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(supervisor, "_spawn_background_loop",
                        lambda *a, **k: 11)
    assert paths.catalog_guid(tmp_path).guid is None
    assert cli.main(["start", "--name", "alpha"]) == 0
    out = capsys.readouterr().out
    assert "registered this directory as a project" in out
    assert paths.catalog_guid(tmp_path).guid


def test_start_spawns_the_background_supervisor(monkeypatch, capsys):
    import supervisor
    seen = {}

    def fake(instance, copilot_args, is_fresh, cwd=None):
        seen.update(name=instance.display_name, args=list(copilot_args),
                    fresh=is_fresh)
        return 4242

    monkeypatch.setattr(supervisor, "_spawn_background_loop", fake)
    assert cli.main(["start", "--name", "alpha", "--agent", "test:agent"]) == 0
    assert seen == {"name": "alpha", "args": ["--agent", "test:agent"],
                    "fresh": False}
    assert "started alpha (pid 4242)" in capsys.readouterr().out


def test_start_does_not_claim_success_when_the_supervisor_died(monkeypatch,
                                                               capsys):
    import supervisor
    import supervisor_control
    monkeypatch.setattr(supervisor, "_spawn_background_loop",
                        lambda *a, **k: 4242)
    monkeypatch.setattr(supervisor_control, "launch_status",
                        lambda inst, pid, **k: ("dead", pid))
    assert cli.main(["start", "--name", "alpha"]) == 1
    captured = capsys.readouterr()
    assert "exited before the supervisor published" in captured.err
    assert "started alpha" not in captured.out


def test_start_does_not_treat_unknown_as_success(monkeypatch, capsys):
    import supervisor
    import supervisor_control
    monkeypatch.setattr(supervisor, "_spawn_background_loop",
                        lambda *a, **k: 7)
    monkeypatch.setattr(supervisor_control, "launch_status",
                        lambda inst, pid, **k: ("unknown", pid))
    assert cli.main(["start", "--name", "alpha"]) == 1
    captured = capsys.readouterr()
    assert "could not confirm supervisor for alpha" in captured.err
    assert "started alpha" not in captured.out


def test_start_accepts_a_positional_name(monkeypatch, capsys):
    import supervisor
    seen = {}

    def fake(instance, copilot_args, is_fresh, cwd=None):
        seen.update(name=instance.display_name, args=list(copilot_args))
        return 1

    monkeypatch.setattr(supervisor, "_spawn_background_loop", fake)
    assert cli.main(["start", "alpha", "--agent", "test:agent"]) == 0
    assert seen == {"name": "alpha", "args": ["--agent", "test:agent"]}


def test_start_keeps_the_parent_name_past_the_terminator(monkeypatch):
    import supervisor
    seen = {}

    def fake(instance, copilot_args, is_fresh, cwd=None):
        seen.update(name=instance.display_name, args=list(copilot_args))
        return 1

    monkeypatch.setattr(supervisor, "_spawn_background_loop", fake)
    assert cli.main([
        "start", "--name", "alpha", "--", "--name=beta", "some text",
    ]) == 0
    assert seen["name"] == "alpha"
    assert seen["args"] == ["--", "--name=beta", "some text"]


def test_start_without_a_name_uses_the_folder_name(monkeypatch, tmp_path, capsys):
    import supervisor
    seen = {}

    def fake(instance, copilot_args, is_fresh, cwd=None):
        seen.update(name=instance.display_name, cwd=cwd, op_id=instance.id)
        return 1

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(supervisor, "_spawn_background_loop", fake)
    assert cli.main(["start"]) == 0
    assert seen["name"] == tmp_path.name
    assert seen["cwd"] == str(tmp_path.resolve())
    assert seen["op_id"].startswith("op-")


def test_list_prints_running_operators(tmp_path, monkeypatch, capsys):
    import operators
    import supervisor_control
    work = tmp_path / "repo"
    work.mkdir()
    record = operators.create("alpha", work)
    monkeypatch.setattr(supervisor_control, "active_instances",
                        lambda: [record.instance()])
    assert cli.main(["list"]) == 0
    out = capsys.readouterr().out
    assert "Running:" in out and "alpha" in out
    assert "Offline:" in out


def test_list_says_so_when_nothing_is_recorded(capsys):
    assert cli.main(["list"]) == 0
    assert capsys.readouterr().out == (
        "No operators yet. Start one with: operator start\n")


def test_list_delegates_to_the_board_rather_than_rendering_its_own(monkeypatch,
                                                                  capsys):
    """`operator list` had a second, poorer listing of its own in here.

    It printed the display name and nothing else, while the board the
    preamble's stale CAUTION sends an agent to read names the changed files
    too. Two renderers for one command is how they came to disagree, so the
    verb is asserted to own no rendering at all: replacing the board with a
    sentinel must replace everything the command prints, return value and
    output both. Checking only the return value leaves a `_list` that prints
    its own row and then delegates, which is the state this came from, and
    both Gate 2 reviewers found that hole independently.
    """
    from operator_cli import listing

    def sentinel():
        print("the board owns this line")
        return 7

    monkeypatch.setattr(listing, "list_instances", sentinel)
    assert cli.main(["list"]) == 7
    assert capsys.readouterr().out == "the board owns this line\n"


def test_attach_attaches(monkeypatch, tmp_path):
    import operators
    record = operators.create("alpha", tmp_path)
    seen = []
    monkeypatch.setattr(op.MUX, "has_session", lambda session: True)
    monkeypatch.setattr(op.MUX, "attach", lambda session: seen.append(session) or 7)
    assert cli.main(["attach", "alpha"]) == 7
    assert seen == [record.id]


def test_attach_without_a_multiplexer_explains(monkeypatch, capsys):
    from mux import MuxNotFoundError

    def boom(*a, **k):
        raise MuxNotFoundError(
            "No terminal multiplexer found. Install psmux:\n"
            "    winget install --id marlocarlo.psmux")

    import operators
    operators.create("alpha", Path.cwd())
    monkeypatch.setattr(op.MUX, "has_session", boom)
    assert cli.main(["attach", "alpha"]) == 1
    err = capsys.readouterr().err
    assert "Traceback" not in err
    assert "multiplexer" in err.lower()
    assert "Install" in err


def test_attach_without_a_name_refuses(capsys):
    assert cli.main(["attach"]) == 2
    assert "Usage: operator attach NAME" in capsys.readouterr().err


def test_stop_requests_the_supervisor_stop(monkeypatch, capsys):
    import supervisor_control
    import operators
    seen = []
    operators.create("alpha", Path.cwd())
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
    assert "No operator 'nonexistent'" in err


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


def test_recover_delegates(monkeypatch):
    seen = []
    monkeypatch.setattr(cli.recover, "main", lambda argv: seen.append(argv) or 0)
    assert cli.main(["recover", "--all"]) == 0
    assert seen == [["--all"]]


def test_the_console_script_is_declared():
    text = (REPO / "pyproject.toml").read_text(encoding="utf-8")
    assert 'operator = "operator_cli.entry:main"' in text
    assert "operator-fleet" not in text
    assert "operator-seat" not in text
    block = text.split("[project.scripts]", 1)[1].split("[", 1)[0]
    scripts = [line.strip() for line in block.splitlines() if line.strip()]
    assert scripts == ['operator = "operator_cli.entry:main"']


# ── operator handoff ────────────────────────────────────────────


def test_handoff_is_reachable_from_the_menu(monkeypatch, capsys):
    """The menu is the documented way in for a human who has not memorised
    the flags, and `--status` needs a prompt to reach the parser."""
    choice = _choice_for(("handoff",))
    printed = []
    monkeypatch.setattr(cli, "dispatch", lambda argv, **k: printed.append(list(argv)) or 0)
    _tty(monkeypatch, f"{choice}\nalpha\nfinished the sweep\n")
    assert cli.main([]) == 0
    assert printed and printed[-1] == [
        "handoff", "--instance", "alpha", "--status", "finished the sweep"]


def test_the_front_door_bootstraps_from_the_shared_home_helper():
    from operator_cli import home
    assert cli._bootstrap is home._bootstrap
    assert cli._settle_home is home._settle_home


def test_a_clean_stop_stays_listed_offline(tmp_path, capsys):
    import operators
    record = operators.create("alpha", tmp_path)
    record.instance().cleanup_files()
    assert cli.main(["list"]) == 0
    out = capsys.readouterr().out
    assert "Offline:" in out
    assert f"1. alpha  ({record.cwd})" in out
    assert "Running:\n  (none)" in out


def test_start_of_an_offline_operator_reuses_its_id_and_cwd(tmp_path, monkeypatch, capsys):
    import operators
    import supervisor
    import supervisor_control
    record = operators.create("alpha", tmp_path)
    seen = {}

    def fake(instance, copilot_args, is_fresh, cwd=None):
        seen.update(op_id=instance.id, cwd=cwd)
        return 4

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(supervisor, "_spawn_background_loop", fake)
    monkeypatch.setattr(supervisor_control, "launch_status", lambda *a, **k: ("ready", 4))
    assert cli.main(["start", "alpha"]) == 0
    assert seen == {"op_id": record.id, "cwd": record.cwd}
    assert "started alpha (pid 4)" in capsys.readouterr().out


def test_an_omitted_name_refuses_a_different_cwd(tmp_path, monkeypatch, capsys):
    import operators
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    operators.create(tmp_path.name, elsewhere)
    monkeypatch.chdir(tmp_path)
    assert cli.main(["start"]) == 2
    err = capsys.readouterr().err
    assert "already works in" in err
    assert "pass a name" in err


def test_rename_keeps_the_operator(tmp_path, capsys):
    import operators
    record = operators.create("alpha", tmp_path)
    assert cli.main(["rename", "alpha", "bravo"]) == 0
    found = operators.find("bravo")
    assert found is not None and found.id == record.id
    assert capsys.readouterr().out.strip() == "renamed alpha to bravo"


def test_delete_refuses_while_running(tmp_path, monkeypatch, capsys):
    import operators
    import supervisor_control
    record = operators.create("alpha", tmp_path)
    monkeypatch.setattr(supervisor_control, "active_instances",
                        lambda: [record.instance()])
    assert cli.main(["delete", "alpha", "--yes"]) == 1
    assert "stop it first: operator stop alpha" in capsys.readouterr().err
    assert operators.find("alpha") is not None


def test_delete_without_yes_and_no_tty_refuses(tmp_path, capsys):
    import operators
    operators.create("alpha", tmp_path)
    assert cli.main(["delete", "alpha"]) == 2
    assert "pass --yes to delete without a terminal" in capsys.readouterr().err
    assert operators.find("alpha") is not None


def test_delete_yes_removes_record_state_and_handoff(tmp_path, monkeypatch, capsys):
    import operators
    from operator_cli import project
    monkeypatch.chdir(tmp_path)
    assert project.ensure_registered()[0] == 0
    record = operators.create("alpha", tmp_path)
    inst = record.instance()
    inst.state_file.parent.mkdir(parents=True, exist_ok=True)
    inst.state_file.write_text("kept", encoding="utf-8")
    runner_log = inst.state_file.with_name(f"{record.id}.runner.log")
    runner_log.write_text("[runner] copilot exited rc=0", encoding="utf-8")
    handoff = paths.project_handoff_file(tmp_path, record.id)
    assert isinstance(handoff, Path)
    handoff.parent.mkdir(parents=True, exist_ok=True)
    handoff.write_text("bye", encoding="utf-8")
    assert cli.main(["delete", "alpha", "--yes"]) == 0
    assert operators.find("alpha") is None
    assert not inst.state_file.exists()
    assert not runner_log.exists()
    assert not handoff.exists()
    assert not paths.catalog_guid(tmp_path).guid


def test_delete_keeps_the_catalog_while_another_operator_shares_the_cwd(
        tmp_path, monkeypatch):
    import operators
    from operator_cli import project
    monkeypatch.chdir(tmp_path)
    assert project.ensure_registered()[0] == 0
    guid = paths.catalog_guid(tmp_path).guid
    operators.create("alpha", tmp_path)
    operators.create("bravo", tmp_path)
    assert cli.main(["delete", "alpha", "--yes"]) == 0
    assert operators.find("bravo") is not None
    assert paths.catalog_guid(tmp_path).guid == guid
    assert paths.project_dir(guid).is_dir()
    assert cli.main(["delete", "bravo", "--yes"]) == 0
    assert not paths.catalog_guid(tmp_path).guid
    assert not paths.project_dir(guid).exists()
