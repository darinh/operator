"""Start, rename, and delete through the verb module, not only the front door."""
from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

import operators
from operator_cli import lifecycle

PREAMBLE_HEAD = "You are running unattended under the operator supervisor"


@pytest.fixture
def launched(monkeypatch, tmp_path):
    import launch
    import op
    import supervisor
    import supervisor_control
    from operator_cli import supervise
    argvs: list[list[str]] = []

    def record(inst, argv, cwd, n):
        argvs.append(list(argv))
        inst.stop_marker.touch()
        return inst.spec_file

    def spawn(instance, copilot_args, is_fresh, cwd=None):
        child = ["--_supervise", "--loop", "--id", instance.id, *copilot_args]
        assert supervise.main(child) == 0
        return 1

    monkeypatch.setattr(launch, "write_launch_spec", record)
    monkeypatch.setattr(launch, "copilot_executable", lambda: "copilot")
    monkeypatch.setattr(op.Instance, "copilot_pid", lambda self: 1)
    monkeypatch.setattr(op, "stop_session_gracefully", lambda instance: None)
    monkeypatch.setattr(op, "COPILOT_LOG_DIR", tmp_path / "logs")
    monkeypatch.setattr(supervisor, "_spawn_background_loop", spawn)
    monkeypatch.setattr(supervisor_control, "launch_status",
                        lambda inst, pid, **k: ("ready", pid))
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(work)
    return argvs


def _prompt(argv: list[str]) -> str:
    assert argv.count("-i") == 1, argv
    return argv[argv.index("-i") + 1]


@pytest.mark.parametrize("words", [
    ["fix", "the", "flaky", "test"],
    ["fix the flaky test"],
    ["--", "fix", "the", "flaky", "test"],
])
def test_the_words_after_the_name_are_the_task(launched, capsys, words):
    assert lifecycle.start(["alpha", *words]) == 0
    assert "started alpha (pid 1)" in capsys.readouterr().out
    [argv] = launched
    prompt = _prompt(argv)
    assert prompt.startswith(PREAMBLE_HEAD)
    assert prompt.endswith(" Task: fix the flaky test")
    assert not {"fix", "the", "flaky", "test", "fix the flaky test",
                "--"} & set(argv), argv


def test_a_copilot_option_keeps_its_value_beside_a_task(launched):
    assert lifecycle.start(["alpha", "--model", "claude-haiku-4.5", "fix", "it"]) == 0
    [argv] = launched
    assert argv[argv.index("--model") + 1] == "claude-haiku-4.5"
    assert _prompt(argv).endswith(" Task: fix it")
    assert not {"fix", "it", "--"} & set(argv), argv


def test_a_task_that_starts_with_a_dash_stays_a_task(launched):
    assert lifecycle.start(["alpha", "--", "--fresh", "start", "over"]) == 0
    [argv] = launched
    assert _prompt(argv).endswith(" Task: --fresh start over")
    assert not {"--fresh", "start", "over", "--"} & set(argv), argv


def test_no_task_is_just_the_preamble(launched):
    assert lifecycle.start(["alpha"]) == 0
    [argv] = launched
    prompt = _prompt(argv)
    assert prompt.startswith(PREAMBLE_HEAD)
    assert "Task:" not in prompt


def test_a_person_starting_an_operator_is_recorded_as_its_parent(launched, monkeypatch):
    import process_tree
    monkeypatch.setattr(process_tree, "ancestry", lambda pid: [4321, 1])
    assert lifecycle.start(["alpha"]) == 0
    record = operators.find("alpha")
    assert (record.parent, record.started_by_pid) == (operators.HUMAN, 4321)


def test_an_agent_starting_an_operator_is_recorded_as_its_parent(
        launched, monkeypatch, tmp_path):
    import json
    import op
    import process_identity
    import process_tree
    lead = operators.create("lead", tmp_path)
    op.Instance(lead.id).custody_file.write_text(
        json.dumps({"pid": 500, "start": "win:1", "session": 2}), encoding="utf-8")
    monkeypatch.setattr(process_tree, "ancestry", lambda pid: [77, 500, 1])
    monkeypatch.setattr(process_identity, "process_start_token", lambda pid: "win:1")
    assert lifecycle.start(["scout", "look around"]) == 0
    record = operators.find("scout")
    assert (record.parent, record.started_by_pid) == (lead.id, 500)


def test_start_refuses_when_it_cannot_tell_who_is_asking(launched, monkeypatch, capsys):
    import process_tree
    monkeypatch.setattr(process_tree, "ancestry", lambda pid: None)
    assert lifecycle.start(["alpha"]) == 1
    assert capsys.readouterr().err == (
        "operator start: could not read the process table\n")
    assert operators.find("alpha") is None


def test_rename_prints_the_old_and_new_names(tmp_path, capsys):
    record = operators.create("alpha", tmp_path)
    assert lifecycle.rename(["alpha", "bravo"]) == 0
    assert operators.find("bravo").id == record.id
    assert capsys.readouterr().out.strip() == "renamed alpha to bravo"


def test_start_refuses_when_the_recorded_directory_is_gone(
        tmp_path, monkeypatch, capsys):
    import supervisor
    gone = tmp_path / "gone"
    gone.mkdir()
    operators.create("alpha", gone)
    gone.rmdir()
    monkeypatch.chdir(tmp_path)

    def boom(*_a, **_k):
        raise AssertionError("spawned")

    monkeypatch.setattr(supervisor, "_spawn_background_loop", boom)
    assert lifecycle.start(["alpha"]) == 1
    err = capsys.readouterr().err
    assert err == (
        "The directory 'alpha' was working in no longer exists:\n"
        f"  {gone.resolve()}\n"
    )
    assert "Traceback" not in err


def test_an_invalid_name_does_not_register_the_project(tmp_path, monkeypatch, capsys):
    import paths
    monkeypatch.chdir(tmp_path)
    catalog = paths.project_catalog_path()
    before = catalog.read_text(encoding="utf-8") if catalog.exists() else ""
    assert lifecycle.start(["--name", "-bad"]) == 2
    after = catalog.read_text(encoding="utf-8") if catalog.exists() else ""
    assert after == before
    assert "cannot start with -" in capsys.readouterr().err


def test_default_name_is_the_operator_already_working_here(tmp_path, monkeypatch):
    here = tmp_path / "demo"
    here.mkdir()
    operators.create("alpha", here)
    monkeypatch.chdir(here)
    assert lifecycle.default_name() == "alpha"


def test_default_name_is_this_directory_when_no_operator_works_here(
        tmp_path, monkeypatch):
    here, there = tmp_path / "demo", tmp_path / "elsewhere"
    here.mkdir()
    there.mkdir()
    operators.create("alpha", there)
    monkeypatch.chdir(here)
    assert lifecycle.default_name() == "demo"


def _running_alpha(monkeypatch, tmp_path) -> list:
    """alpha runs in this directory and has a session. Attach answers 7."""
    import op
    import supervisor_control
    record = operators.create("alpha", tmp_path)
    op.MUX.sessions[record.instance().session] = {
        "cwd": str(tmp_path), "argv": [], "remain_on_exit": False, "dead": False}
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(supervisor_control, "active_instances",
                        lambda: [record.instance()])
    attached: list = []
    monkeypatch.setattr(lifecycle, "attach",
                        lambda rest: attached.append(rest) or 7)
    return attached


def test_attach_on_a_running_operator_attaches_and_returns_the_attach_code(
        tmp_path, monkeypatch, capsys):
    attached = _running_alpha(monkeypatch, tmp_path)
    assert lifecycle.start(["alpha", "--attach"]) == 7
    assert attached == [["alpha"]]
    assert capsys.readouterr().out == "alpha is already running\n"


@pytest.mark.parametrize("more", [["--fresh"], ["fix", "it"], ["--model", "gpt"]])
def test_a_running_operator_refuses_what_attaching_would_drop(
        tmp_path, monkeypatch, capsys, more):
    attached = _running_alpha(monkeypatch, tmp_path)
    assert lifecycle.start(["alpha", "--attach", *more]) == 1
    assert attached == []
    seen = capsys.readouterr()
    assert (seen.out, seen.err) == ("", "alpha is already running\n")


def test_a_name_with_a_space_is_quoted_in_what_it_tells_you_to_type(
        tmp_path, monkeypatch, capsys):
    import supervisor_control
    spaced = operators.create("my op", tmp_path)
    operators.create("bravo", tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(supervisor_control, "active_instances",
                        lambda: [spaced.instance()])
    monkeypatch.setattr(supervisor_control, "wait_for_session", lambda instance: False)
    assert lifecycle.start([]) == 2
    assert lifecycle.start(["my op", "--attach"]) == 1
    assert lifecycle.delete(["my op", "--yes"]) == 1
    assert capsys.readouterr().err.splitlines() == [
        '2 operators work here: bravo, "my op"',
        "pass a name: operator start NAME",
        'my op has no session to attach to yet. Try again: operator attach "my op"',
        'stop it first: operator stop "my op"',
    ]


@pytest.mark.parametrize("name", ["$HOME", 'a"b', "%PATH%", "back`tick", "wow!",
                                  "dir\\", "\u201csmart\u201d", "\u201elow"])
def test_a_name_a_shell_would_change_is_typed_as_its_id(
        tmp_path, monkeypatch, capsys, name):
    import supervisor_control
    record = operators.create(name, tmp_path)
    monkeypatch.setattr(supervisor_control, "active_instances",
                        lambda: [record.instance()])
    monkeypatch.setattr(supervisor_control, "wait_for_session", lambda instance: False)
    assert lifecycle.start([name, "--attach"]) == 1
    assert lifecycle.delete([name, "--yes"]) == 1
    assert capsys.readouterr().err.splitlines() == [
        f"{name} has no session to attach to yet. "
        f"Try again: operator attach {record.id}",
        f"stop it first: operator stop {record.id}",
    ]


def test_the_operators_here_show_a_name_beside_the_id_to_type(
        tmp_path, monkeypatch, capsys):
    home = operators.create("$HOME", tmp_path)
    operators.create("bravo", tmp_path)
    monkeypatch.chdir(tmp_path)
    assert lifecycle.start([]) == 2
    assert capsys.readouterr().err.splitlines() == [
        f"2 operators work here: {home.id} ($HOME), bravo",
        "pass a name: operator start NAME",
    ]


def test_delete_keeps_the_project_when_a_sibling_record_will_not_load(
        tmp_path, monkeypatch):
    import paths
    from operator_cli import project
    monkeypatch.chdir(tmp_path)
    assert project.ensure_registered()[0] == 0
    guid = paths.catalog_guid(tmp_path).guid
    project_dir = paths.project_dir(guid)
    kept = project_dir / "handoff" / "sibling.md"
    kept.parent.mkdir(parents=True)
    kept.write_text("stay", encoding="utf-8")
    operators.create("alpha", tmp_path)
    sibling = operators.create("bravo", tmp_path)
    (operators.records_dir() / f"{sibling.id}.json").write_text("", encoding="utf-8")
    assert lifecycle.delete(["alpha", "--yes"]) == 0
    assert paths.catalog_guid(tmp_path).guid == guid
    assert project_dir.is_dir()
    assert kept.read_text(encoding="utf-8") == "stay"
    assert operators.find("alpha") is None


def _repo_with_worktree(tmp_path):
    import subprocess
    primary, linked = tmp_path / "primary", tmp_path / "linked"
    primary.mkdir()
    git = ["git", "-c", "user.name=t", "-c", "user.email=t@t",
           "-c", "commit.gpgsign=false"]
    for args in (["init", "-q"], ["commit", "-q", "--allow-empty", "-m", "x"],
                 ["worktree", "add", "-q", str(linked)]):
        subprocess.run(git + args, cwd=primary, check=True)
    return primary, linked


def test_delete_keeps_the_project_while_another_checkout_of_it_has_an_operator(
        tmp_path, monkeypatch):
    import paths
    from operator_cli import project
    primary, linked = _repo_with_worktree(tmp_path)
    monkeypatch.chdir(primary)
    assert project.ensure_registered()[0] == 0
    guid = paths.catalog_guid(primary).guid
    assert paths.catalog_guid(linked).guid == guid
    operators.create("main-seat", primary)
    operators.create("tree-seat", linked)
    kept = paths.project_dir(guid) / "kept.md"
    kept.write_text("still here", encoding="utf-8")
    assert lifecycle.delete(["tree-seat", "--yes"]) == 0
    assert paths.catalog_guid(primary).guid == guid
    assert kept.read_text(encoding="utf-8") == "still here"
    assert lifecycle.delete(["main-seat", "--yes"]) == 0
    assert not paths.catalog_guid(primary).guid


def test_delete_keeps_the_project_while_an_operator_in_a_removed_checkout_remains(
        tmp_path, monkeypatch):
    import subprocess
    import paths
    from operator_cli import project
    primary, linked = _repo_with_worktree(tmp_path)
    monkeypatch.chdir(primary)
    assert project.ensure_registered()[0] == 0
    guid = paths.catalog_guid(primary).guid
    operators.create("main-seat", primary)
    operators.create("tree-seat", linked)
    subprocess.run(["git", "worktree", "remove", str(linked)], cwd=primary, check=True)
    assert lifecycle.delete(["main-seat", "--yes"]) == 0
    assert paths.catalog_guid(primary).guid == guid
    assert paths.project_dir(guid).is_dir()


def test_delete_keeps_the_record_when_a_file_cannot_be_removed(
        tmp_path, monkeypatch, capsys):
    import instance as instance_mod
    record = operators.create("alpha", tmp_path)
    state = record.instance().state_file
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text("stay", encoding="utf-8")
    real = instance_mod.remove_file

    def fail_state(path):
        if path == state:
            return False
        return real(path)

    monkeypatch.setattr(instance_mod, "remove_file", fail_state)
    assert lifecycle.delete(["alpha", "--yes"]) == 1
    captured = capsys.readouterr()
    assert str(state) in captured.err
    assert "deleted alpha" not in captured.out
    assert operators.find("alpha") is not None
    assert state.read_text(encoding="utf-8") == "stay"


def test_delete_keeps_the_record_when_its_mailbox_cannot_be_removed(
        tmp_path, monkeypatch, capsys):
    import mail
    record = operators.create("alpha", tmp_path)
    mail.post(record.id, {"from": "human", "from_name": "human", "to": record.id,
                          "relation": "your parent", "text": "stay", "sent": "x"})
    monkeypatch.setattr(mail, "forget", lambda recipient: False)
    assert lifecycle.delete(["alpha", "--yes"]) == 1
    assert str(mail.box(record.id)) in capsys.readouterr().err
    assert operators.find("alpha") is not None
    assert mail.waiting(record.id) == 1


def test_delete_keeps_the_record_when_the_catalog_cannot_be_read(
        tmp_path, monkeypatch, capsys):
    import paths
    from config import CATALOG_UNREADABLE
    record = operators.create("alpha", tmp_path)
    monkeypatch.setattr(paths, "project_handoff_file",
                        lambda cwd, op_id: CATALOG_UNREADABLE)
    assert lifecycle.delete(["alpha", "--yes"]) == 1
    assert capsys.readouterr().err == (
        "could not remove the handoff (project catalog unreadable)\n")
    assert operators.find("alpha") == record


def test_delete_refuses_while_a_supervisor_is_starting(tmp_path, capsys):
    record = operators.create("alpha", tmp_path)
    startup = record.instance().loop_startup_file
    startup.parent.mkdir(parents=True, exist_ok=True)
    startup.write_text("0", encoding="utf-8")
    assert lifecycle.delete(["alpha", "--yes"]) == 1
    assert capsys.readouterr().err.strip() == "stop it first: operator stop alpha"
    assert operators.find("alpha") is not None
    assert startup.is_file()


def test_a_second_create_is_refused_while_the_records_lock_is_held(tmp_path):
    first = operators.create("alpha", tmp_path)
    lock = operators.records_dir() / ".lock"
    fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_RDWR)
    try:
        try:
            operators.create("bravo", tmp_path)
        except operators.BadName as exc:
            assert str(exc) == "could not lock operator records"
        else:
            raise AssertionError("second create succeeded while the lock was held")
    finally:
        os.close(fd)
        os.unlink(lock)
    assert [op.id for op in operators.all_operators()] == [first.id]
    assert operators.find("bravo") is None


def test_a_lock_left_by_a_dead_process_does_not_block_create(tmp_path):
    lock = operators.records_dir() / ".lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text("", encoding="utf-8")
    old = time.time() - 60
    os.utime(lock, (old, old))
    made = operators.create("alpha", tmp_path)
    assert operators.find("alpha") == made
    assert not lock.exists()


def test_delete_refuses_a_running_operator(tmp_path, monkeypatch, capsys):
    import supervisor_control
    record = operators.create("alpha", tmp_path)
    monkeypatch.setattr(supervisor_control, "active_instances",
                        lambda: [record.instance()])
    assert lifecycle.delete(["alpha", "--yes"]) == 1
    assert capsys.readouterr().err.strip() == "stop it first: operator stop alpha"
    assert operators.find("alpha") is not None


def _family(tmp_path):
    """lead, its child scout, scout's child deep, and other, whom a person started."""
    lead = operators.create("lead", tmp_path)
    scout = operators.create("scout", tmp_path, parent=lead.id)
    deep = operators.create("deep", tmp_path, parent=scout.id)
    other = operators.create("other", tmp_path)
    return lead, scout, deep, other


def test_an_agent_starts_its_child_where_it_works_not_where_its_shell_stands(
        launched, monkeypatch, tmp_path):
    from test_handoff import _seat
    (tmp_path / "lead").mkdir()
    lead = operators.create("lead", tmp_path / "lead")
    _seat(monkeypatch, lead)
    assert lifecycle.start(["scout", "look around"]) == 0
    scout = operators.find("scout")
    assert (scout.parent, scout.cwd) == (lead.id, lead.cwd)
    assert os.getcwd() != lead.cwd


def test_an_agent_reaches_only_the_operators_it_started(
        launched, monkeypatch, tmp_path, capsys):
    from test_handoff import _seat
    lead, scout, deep, other = _family(tmp_path)
    _seat(monkeypatch, scout)
    for verb, argv in ((lifecycle.start, ["lead"]), (lifecycle.start, ["other"]),
                       (lifecycle.start, ["scout"]), (lifecycle.stop, ["lead"]),
                       (lifecycle.stop, ["other"]), (lifecycle.delete, ["lead", "--yes"]),
                       (lifecycle.delete, ["other", "--yes"])):
        assert verb(argv) == 2, argv
    assert lifecycle.attach(["deep"]) == 2
    assert lifecycle.rename(["deep", "renamed"]) == 2
    assert lifecycle.start([]) == 2
    assert lifecycle.start(["deep", "--attach"]) == 2
    assert launched == []
    assert sorted(op.name for op in operators.all_operators()) == [
        "deep", "lead", "other", "scout"]
    said = capsys.readouterr().err.splitlines()
    assert said[0] == ("operator start: lead is not your child. "
                       "An operator may start only the operators it started.")
    assert said[-4:] == [
        "operator attach: only a person can do this, not an operator",
        "operator rename: only a person can do this, not an operator",
        'operator start: an operator must name the child it starts: operator start NAME "..."',
        "operator start: only a person can attach, so an operator cannot pass --attach",
    ]


def test_an_agent_restarts_and_deletes_its_own_stopped_child(
        launched, monkeypatch, tmp_path):
    from test_handoff import _seat
    from operator_cli import project
    monkeypatch.chdir(tmp_path)
    assert project.ensure_registered()[0] == 0
    lead, scout, deep, other = _family(tmp_path)
    _seat(monkeypatch, scout)
    assert lifecycle.start(["deep", "carry on"]) == 0
    assert len(launched) == 1
    assert lifecycle.delete(["deep", "--yes"]) == 0
    assert operators.find("deep") is None


def test_the_fifth_running_child_is_refused_and_nothing_is_recorded(
        launched, monkeypatch, tmp_path, capsys):
    import supervisor_control
    from test_handoff import _seat
    lead = operators.create("lead", tmp_path)
    kids = [operators.create(f"kid{n}", tmp_path, parent=lead.id) for n in range(4)]
    monkeypatch.setattr(supervisor_control, "active_instances",
                        lambda: [kid.instance() for kid in kids])
    _seat(monkeypatch, lead)
    assert lifecycle.start(["kid4"]) == 2
    assert operators.find("kid4") is None
    assert "OPERATOR_MAX_CHILDREN allows 4" in capsys.readouterr().err
    monkeypatch.setenv("OPERATOR_MAX_CHILDREN", "5")
    assert lifecycle.start(["kid4"]) == 0


def test_two_children_started_at_once_cannot_both_take_the_last_place(
        launched, monkeypatch, tmp_path, capsys):
    """An agent can run two starts at once. Each counts the running children
    and then launches, so with no lock held across both, each counts three of
    four and both launch. The first launch here waits until the second start
    has counted, or a second has passed."""
    import threading
    import supervisor
    import supervisor_control
    from operator_cli import family
    from test_handoff import _seat
    lead = operators.create("lead", tmp_path)
    running = [operators.create(f"kid{n}", tmp_path, parent=lead.id).instance()
               for n in range(3)]
    monkeypatch.setattr(supervisor_control, "active_instances", lambda: list(running))
    launching, counted = threading.Event(), threading.Event()
    real_cap = family.cap_problem

    def cap_problem(who, new):
        if launching.is_set():
            counted.set()
        return real_cap(who, new)

    def spawn(instance, copilot_args, is_fresh, cwd=None):
        launching.set()
        counted.wait(1.0)
        running.append(instance)
        return 1

    monkeypatch.setattr(family, "cap_problem", cap_problem)
    monkeypatch.setattr(supervisor, "_spawn_background_loop", spawn)
    _seat(monkeypatch, lead)
    codes = []
    starts = [threading.Thread(target=lambda n=n: codes.append(lifecycle.start([f"new{n}"])))
              for n in range(2)]
    for each in starts:
        each.start()
    for each in starts:
        each.join(10)
    assert sorted(codes) == [0, 2]
    assert len(running) == 4
    assert "OPERATOR_MAX_CHILDREN allows 4" in capsys.readouterr().err


def test_stop_takes_every_running_operator_under_the_one_named(
        monkeypatch, tmp_path, capsys):
    import supervisor_control
    from test_handoff import _seat
    lead, scout, deep, other = _family(tmp_path)
    idle = operators.create("idle", tmp_path, parent=lead.id)
    running = [lead, scout, deep, other]
    monkeypatch.setattr(supervisor_control, "active_instances",
                        lambda: [each.instance() for each in running])
    stopped = []
    monkeypatch.setattr(supervisor_control, "stop_all",
                        lambda insts: stopped.append([i.display_name for i in insts]))
    assert lifecycle.stop(["lead"]) == 0
    assert idle.name not in stopped[0]
    _seat(monkeypatch, lead)
    assert lifecycle.stop(["scout"]) == 0
    assert stopped == [["lead", "scout", "deep"], ["scout", "deep"]]
    assert capsys.readouterr().out.splitlines() == [
        "stop requested for lead", "stop requested for scout",
        "stop requested for deep", "stop requested for scout",
        "stop requested for deep"]


def test_dir_starts_an_operator_in_another_checkout_of_this_project(
        launched, monkeypatch, tmp_path, capsys):
    import paths
    primary, linked = _repo_with_worktree(tmp_path)
    plain = tmp_path / "plain"
    plain.mkdir()
    monkeypatch.chdir(primary)
    assert lifecycle.start(["beta", "--dir", str(linked)]) == 0
    beta = operators.find("beta")
    assert Path(beta.cwd).resolve() == linked.resolve()
    assert paths.catalog_guid(linked).guid == paths.catalog_guid(primary).guid
    assert lifecycle.start(["gamma", f"--dir={plain}"]) == 2
    assert operators.find("gamma") is None
    assert lifecycle.start(["beta", "--dir", str(primary)]) == 2
    err = [line for line in capsys.readouterr().err.splitlines()
           if not line.startswith("[operator ")]
    assert err[0].endswith("plain is not a worktree of this project.")
    assert err[-1] == f"operator start: beta works in {beta.cwd}. Leave out --dir to start it there."
