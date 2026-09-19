"""The process a supervisor runs in, and the entry point that had gone missing.

`supervisor._spawn_background_loop` starts a supervisor by launching this
module. It used to launch `supervisor.py` itself, by `__file__` -- and nothing
in that file has ever read `--_supervise`, because the argument handling stayed
behind in the 9,120-line module the supervision loop was ported out of.

So every supervisor the kernel spawned ran a file with no entry point and
exited 0. Nothing reported it, because exiting 0 is what success looks like.
The visible consequence was in `restart_loop`, which asks the old supervisor to
detach *before* spawning the replacement: the session kept running and its
supervisor was simply gone. Measured before the fix -- running the exact
command `_spawn_background_loop` built produced no output, no pid file, no log
line, and exit 0.

**These call `main()` in-process on purpose.** A subprocess does not inherit
`conftest`'s multiplexer guard, so it drives the developer's real tmux: an
earlier draft of this file created two live sessions on this machine before an
assertion caught it. That is the hazard `conftest` documents at length,
reintroduced by the one kind of test that escapes it. The single subprocess
case below is the refusal, which returns before any multiplexer, home or
instance is touched.
"""
from __future__ import annotations

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


# ── the entry point exists and arrives ───────────────────────────


def test_the_entry_point_reaches_the_supervision_loop(home, monkeypatch):
    """The regression itself, stated as the property it broke.

    Not asserted by importing `main` -- a module can define one and never call
    it -- but by requiring that the spawner's own arguments arrive at
    `run_loop_mode` intact. Before the fix nothing did.
    """
    import supervisor

    seen = {}

    def record(instance, user_args, is_fresh, adopt=False):
        seen.update(name=instance.display_name, args=user_args,
                    fresh=is_fresh, adopt=adopt)
        return 0

    monkeypatch.setattr(supervisor, "run_loop_mode", record)
    rc = supervise.main(["--_supervise", "--loop", "--name", "probe",
                         "--agent", "test:agent"])

    assert rc == 0
    assert seen.get("name") == "probe", (
        f"the entry point did not reach the supervision loop: {seen}")
    assert seen["args"] == ["--agent", "test:agent"]


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


def test_fresh_and_adopt_reach_the_loop(home, monkeypatch):
    """`restart_loop` sets `--adopt`; a supervisor that dropped it would
    relaunch over the session it was supposed to take over."""
    import supervisor

    seen = {}
    monkeypatch.setattr(supervisor, "run_loop_mode",
                        lambda i, a, f, adopt=False: seen.update(fresh=f, adopt=adopt))
    supervise.main(["--_supervise", "--loop", "--name", "x", "--fresh", "--adopt"])
    assert seen == {"fresh": True, "adopt": True}


def test_the_exit_code_of_the_loop_is_the_exit_code_of_the_process(home,
                                                                  monkeypatch):
    """The breakers report through it. `EXIT_NO_PROGRESS` and
    `EXIT_UNACCOUNTED` are how an unattended run says why it stopped, and a
    supervisor that swallowed them would make every ending look the same."""
    import supervisor

    monkeypatch.setattr(supervisor, "run_loop_mode",
                        lambda *a, **k: op.EXIT_NO_PROGRESS)
    assert supervise.main(["--_supervise", "--name", "x"]) == op.EXIT_NO_PROGRESS


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
    for flag in ("--_supervise", "--loop", "--fresh", "--adopt"):
        assert flag in source, f"{flag} is no longer sent by the spawner"
        _, passed_on, _, _ = supervise.parse(["--_supervise", "--name", "x", flag])
        assert flag not in passed_on, (
            f"{flag} was passed through to Copilot instead of being handled")
    assert '"--name", instance.display_name' in source, (
        "the spawner no longer sends --name the way this target reads it")


# ── refusals ─────────────────────────────────────────────────────


def test_running_it_as_a_command_is_refused():
    """It is how a supervisor is started, not something a human runs.

    The one subprocess test here, and safe as one: it returns before any
    multiplexer, home or instance is touched.
    """
    result = subprocess.run(
        [sys.executable, "-m", "operator_cli.supervise", "--loop", "--name", "x"],
        capture_output=True, encoding="utf-8", errors="replace", timeout=120)
    assert result.returncode == 2
    assert "not a command" in result.stderr


def test_a_supervisor_without_a_name_is_refused(home):
    """An unnamed instance would address another instance's state files."""
    with pytest.raises(SystemExit):
        supervise.main(["--_supervise", "--loop"])


@pytest.mark.parametrize("argv", [
    pytest.param(["--_supervise", "--name", ""], id="empty value"),
    pytest.param(["--_supervise", "--name", "   "], id="whitespace only"),
    pytest.param(["--_supervise", "--name"], id="flag with nothing after it"),
    pytest.param(["--_supervise", "--name="], id="empty --name="),
])
def test_an_unusable_name_is_refused(argv, home):
    with pytest.raises(SystemExit):
        supervise.main(argv)


# ── the parser ───────────────────────────────────────────────────


def test_the_name_is_read_from_either_spelling():
    for argv in (["--_supervise", "--name", "seat"],
                 ["--_supervise", "--name=seat"]):
        name, _, _, _ = supervise.parse(argv)
        assert name == "seat", argv


def test_they_are_off_unless_asked_for():
    _, _, fresh, adopt = supervise.parse(["--_supervise", "--name", "x"])
    assert not fresh and not adopt


def test_everything_else_is_passed_through_untouched():
    """Copilot's own flags are not this parser's to interpret, or to reject.

    argparse would refuse them, and the flags it would collide with are not
    ours to rename.
    """
    _, args, _, _ = supervise.parse(
        ["--_supervise", "--loop", "--name", "x",
         "--agent", "test:agent", "--effort", "high", "--weird-future-flag"])
    assert args == ["--agent", "test:agent", "--effort", "high",
                    "--weird-future-flag"]


def test_the_instance_name_is_not_passed_on_to_copilot():
    """It is addressed to the supervisor, and Copilot would reject it."""
    _, args, _, _ = supervise.parse(
        ["--_supervise", "--loop", "--name", "seat", "--agent", "a"])
    assert "seat" not in args and "--name" not in args
