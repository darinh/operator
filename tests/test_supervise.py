"""The process a supervisor runs in.

`supervisor._spawn_background_loop` starts a supervisor by launching this
module.

**These call `main()` in-process on purpose.** A subprocess does not inherit
`conftest`'s multiplexer guard, so it drives the developer's real tmux: an
earlier draft of this file created two live sessions on this machine before an
assertion caught it. That is the hazard `conftest` documents at length,
reintroduced by the one kind of test that escapes it. The single subprocess
case below is the refusal, which returns before any multiplexer, home or
instance is touched.
"""
from __future__ import annotations

import json
import subprocess
import sys

import pytest

import op
from operator_cli import supervise


@pytest.fixture
def home(tmp_path, monkeypatch):
    restart = tmp_path / "restart"
    restart.mkdir(parents=True)
    monkeypatch.setattr(op, "OPERATOR_HOME", tmp_path)
    monkeypatch.setattr(op, "RESTART_DIR", restart)
    monkeypatch.setattr(op, "LOG_FILE", tmp_path / "operator.log")
    monkeypatch.setattr(op, "POLL_INTERVAL", 0)
    workdir = tmp_path / "work"
    workdir.mkdir()
    monkeypatch.chdir(workdir)
    return tmp_path


def _plant(op_id: str) -> None:
    directory = op.OPERATOR_HOME / "operators"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{op_id}.json").write_text(json.dumps({
        "id": op_id, "name": op_id, "cwd": str(op.OPERATOR_HOME),
        "created": "2026-01-01T00:00:00Z",
    }), encoding="utf-8")


# ── the entry point exists and arrives ───────────────────────────


def test_the_entry_point_reaches_the_supervision_loop(home, monkeypatch):
    """The regression itself, stated as the property it broke.

    Not asserted by importing `main` -- a module can define one and never call
    it -- but by requiring that the spawner's own arguments arrive at
    `run_loop_mode` intact. Before the fix nothing did.
    """
    import supervisor

    seen = {}

    def record(instance, user_args, is_fresh):
        seen.update(name=instance.display_name, args=user_args,
                    fresh=is_fresh)
        return 0

    monkeypatch.setattr(supervisor, "run_loop_mode", record)
    _plant("probe")
    rc = supervise.main(["--_supervise", "--loop", "--id", "probe",
                         "--agent", "test:agent"])

    assert rc == 0
    assert seen.get("name") == "probe", (
        f"the entry point did not reach the supervision loop: {seen}")
    assert seen["args"] == ["--agent", "test:agent"]


def test_parse_keeps_the_parent_name_when_the_tail_looks_like_flags():
    name, rest, fresh = supervise.parse([
        "--_supervise", "--loop", "--id", "alpha",
        "--", "--name=beta", "some text",
    ])
    assert name == "alpha"
    assert rest == ["--", "--name=beta", "some text"]
    assert fresh is False


def test_parse_does_not_take_fresh_from_the_tail():
    name, rest, fresh = supervise.parse([
        "--_supervise", "--loop", "--id", "alpha", "--", "--fresh",
    ])
    assert name == "alpha"
    assert rest == ["--", "--fresh"]
    assert fresh is False


def test_main_uses_the_spawner_argv_and_ignores_a_name_in_the_tail(
        home, monkeypatch):
    import supervisor
    seen = {}

    def record(instance, user_args, is_fresh):
        seen.update(name=instance.display_name, args=list(user_args),
                    fresh=is_fresh)
        return 0

    monkeypatch.setattr(supervisor, "run_loop_mode", record)
    _plant("alpha")
    argv = ["--_supervise", "--loop", "--id", "alpha",
            "--", "--name=beta", "some text"]
    assert supervise.main(argv) == 0
    assert seen["name"] == "alpha"
    assert seen["args"] == ["--", "--name=beta", "some text"]


def test_the_module_is_runnable_as_a_script():
    """`_spawn_background_loop` runs `-m operator_cli.supervise`.

    A module without a `__main__` guard is importable and not runnable, which
    is exactly the shape of the defect this file exists for.
    """
    from pathlib import Path
    source = Path(supervise.__file__).read_text(encoding="utf-8")
    assert '__name__ == "__main__"' in source, (
        "the module the spawner runs has no entry point, so every supervisor "
        "it starts will exit 0 having done nothing")


def test_fresh_reaches_the_loop(home, monkeypatch):
    """`--fresh` has to arrive at the loop. A flag the entry point dropped
    would be handed to Copilot instead."""
    import supervisor

    seen = {}
    monkeypatch.setattr(supervisor, "run_loop_mode",
                        lambda i, a, f: seen.update(fresh=f))
    _plant("x")
    supervise.main(["--_supervise", "--loop", "--id", "x", "--fresh"])
    assert seen == {"fresh": True}


def test_the_exit_code_of_the_loop_is_the_exit_code_of_the_process(home,
                                                                  monkeypatch):
    """A supervisor that swallowed the loop's code would make every ending
    look the same."""
    import supervisor

    monkeypatch.setattr(supervisor, "run_loop_mode", lambda *a, **k: 7)
    _plant("x")
    assert supervise.main(["--_supervise", "--id", "x"]) == 7


def test_the_arguments_the_spawner_sends_are_the_ones_this_accepts():
    """Pinned against `_spawn_background_loop` rather than a retyped list.

    The two drifting apart is the defect this file exists for, in its smaller
    form: a flag the spawner sends and the target does not understand is
    passed to Copilot instead, silently.
    """
    import inspect

    import supervisor

    source = inspect.getsource(supervisor._spawn_background_loop)
    assert "operator_cli.supervise" in source, (
        "the spawner no longer launches this module; this test is stale")
    for flag in ("--_supervise", "--loop", "--fresh"):
        assert flag in source, f"{flag} is no longer sent by the spawner"
        _, passed_on, _ = supervise.parse(["--_supervise", "--id", "x", flag])
        assert flag not in passed_on, (
            f"{flag} was passed through to Copilot instead of being handled")
    assert '"--id", instance.id' in source, (
        "the spawner no longer sends --id the way this target reads it")


# ── refusals ─────────────────────────────────────────────────────


def test_running_it_as_a_command_is_refused():
    """It is how a supervisor is started, not something a human runs.

    The one subprocess test here, and safe as one: it returns before any
    multiplexer, home or instance is touched.
    """
    result = subprocess.run(
        [sys.executable, "-m", "operator_cli.supervise", "--loop", "--id", "x"],
        capture_output=True, encoding="utf-8", errors="replace", timeout=120)
    assert result.returncode == 2
    assert "not a command" in result.stderr


def test_a_supervisor_without_a_name_is_refused(home):
    """An unnamed instance would address another instance's state files."""
    with pytest.raises(SystemExit):
        supervise.main(["--_supervise", "--loop"])


@pytest.mark.parametrize("argv", [
    pytest.param(["--_supervise", "--id", ""], id="empty value"),
    pytest.param(["--_supervise", "--id", "   "], id="whitespace only"),
    pytest.param(["--_supervise", "--id"], id="flag with nothing after it"),
    pytest.param(["--_supervise", "--id="], id="empty --id="),
])
def test_an_unusable_name_is_refused(argv, home):
    with pytest.raises(SystemExit):
        supervise.main(argv)


# ── the parser ───────────────────────────────────────────────────


def test_the_id_is_read_from_either_spelling():
    for argv in (["--_supervise", "--id", "op-abcdef01"],
                 ["--_supervise", "--id=op-abcdef01"]):
        op_id, _, _ = supervise.parse(argv)
        assert op_id == "op-abcdef01", argv


def test_fresh_is_off_unless_asked_for():
    _, _, fresh = supervise.parse(["--_supervise", "--id", "x"])
    assert not fresh


def test_everything_else_is_passed_through_untouched():
    """Copilot's own flags are not this parser's to interpret, or to reject.

    argparse would refuse them, and the flags it would collide with are not
    ours to rename.
    """
    _, args, _ = supervise.parse(
        ["--_supervise", "--loop", "--id", "x",
         "--agent", "test:agent", "--effort", "high", "--weird-future-flag"])
    assert args == ["--agent", "test:agent", "--effort", "high",
                    "--weird-future-flag"]


def test_the_instance_name_is_not_passed_on_to_copilot():
    """It is addressed to the supervisor, and Copilot would reject it."""
    _, args, _ = supervise.parse(
        ["--_supervise", "--loop", "--id", "op-abcdef01", "--agent", "a"])
    assert "op-abcdef01" not in args and "--id" not in args


def test_supervise_bootstraps_from_the_shared_home_helper():
    from operator_cli import home
    assert supervise._bootstrap is home._bootstrap
