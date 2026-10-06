"""Start, rename, and delete through the verb module, not only the front door."""
from __future__ import annotations

import os
import time

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
    """alpha runs in this directory. Attach is recorded and answers 7."""
    import supervisor_control
    record = operators.create("alpha", tmp_path)
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
