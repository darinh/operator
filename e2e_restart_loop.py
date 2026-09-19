"""End-to-end: restart-loop must swap the supervisor and keep the session.

Runs real processes against a real multiplexer in an isolated operator home,
with a stub `copilot` on PATH so nothing bills. It verifies the property that
matters and that no unit test can prove: after `restart_loop`, the mux session
is the *same* session -- same pane pid -- while the supervisor process behind
it is a different one.

**This harness had been aimed at another repository.** It drove
`copilot_operator.py`, which lives in the sibling `copilot-tools` checkout and
has never existed here, so `main()` could not run at all; only `read_pid` was
reachable, and only because a unit test imports it by path. What it was testing
for was real, and this repository had no way to start a supervisor until
`operator_cli/supervise.py` existed -- which is the defect this harness would
have caught the day it was pointed at the right tree.

It is not part of the pytest suite and must not be. It needs a live
multiplexer, it creates a real session, and it takes tens of seconds; the
suite's `conftest` substitutes the multiplexer precisely so that no test can do
any of that. Run it by hand:

    python e2e_restart_loop.py

Exit 0 means every check passed. The session it creates is named for this
process, so a stale one is identifiable, and it is torn down in a `finally`.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent

#: Named for this process so a run that dies mid-flight leaves something
#: identifiable, and so two runs -- or a run beside the developer's own work --
#: cannot collide on a session name.
NAME = f"e2erestart{os.getpid()}"

failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> bool:
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"  [{detail}]" if detail else ""))
    if not ok:
        failures.append(label)
    return ok


def stub_copilot(bindir: Path) -> None:
    """A `copilot` that just stays alive, so a session can exist to adopt."""
    if os.name == "nt":
        (bindir / "copilot.cmd").write_text(
            "@echo off\r\nping -n 900 127.0.0.1 >nul\r\n", encoding="utf-8")
    else:
        p = bindir / "copilot"
        p.write_text("#!/bin/sh\nsleep 900\n", encoding="utf-8")
        p.chmod(0o755)


def read_pid(path: Path) -> "int | None":
    """The pid from a pid file whose later lines may carry identity stamps.

    `supervisor_records._loop_pid_stamp` writes the pid on the first line and
    `key=value` stamps after it, so reading the whole file as one integer
    would fail on every stamped supervisor and report the loop as never
    coming up.

    ``ValueError`` covers the read as well as the parse: a file damaged into
    invalid UTF-8 raises ``UnicodeDecodeError``, which is a ``ValueError``
    rather than an ``OSError``.
    """
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, ValueError):
        return None
    if not lines:
        return None
    try:
        return int(lines[0].strip())
    except ValueError:
        return None


def wait_for(fn, timeout: float = 60.0, interval: float = 0.5):
    deadline = time.time() + timeout
    while time.time() < deadline:
        value = fn()
        if value:
            return value
        time.sleep(interval)
    return None


def _changed_pid(path: Path, old: "int | None") -> "int | None":
    """The pid in path, but only once it differs from old."""
    pid = read_pid(path)
    return pid if pid and pid != old else None


def pid_alive(pid: "int | None") -> bool:
    if not pid:
        return False
    if os.name == "nt":
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                             capture_output=True,
                             encoding="utf-8", errors="replace")
        # A read that failed must not read as "the process is gone": that
        # answer makes the caller stop waiting and start cleaning up.
        if out.stdout is None:
            return True
        return str(pid) in out.stdout
    try:
        os.kill(pid, 0)
    except (OSError, ProcessLookupError):
        return False
    return True


def kill_pid(pid: int) -> None:
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                       capture_output=True)
    else:
        os.kill(pid, 15)


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="op-e2e-"))
    home = tmp / "operator-home"
    bindir = tmp / "bin"
    project = tmp / "project"
    for d in (home, bindir, project):
        d.mkdir(parents=True)
    stub_copilot(bindir)

    # Set before the kernel is imported, never after: `config.py` resolves
    # `OPERATOR_HOME` at import and derives `RESTART_DIR` from it there, so a
    # home settled afterwards reaches nothing and this would run against the
    # developer's real one. `operator_cli/recover.py` shipped that bug once.
    os.environ["COPILOT_OPERATOR_HOME"] = str(home)
    os.environ["PATH"] = str(bindir) + os.pathsep + os.environ["PATH"]
    os.environ["OPERATOR_NO_TAB_PROGRESS"] = "1"

    sys.path.insert(0, str(REPO))
    from operator_cli.fleet import _bootstrap
    _bootstrap()
    import mux as mux_module
    from instance import Instance
    from supervisor import _spawn_background_loop
    from supervisor_control import _request_supervisor_stop, restart_loop

    mux = mux_module.Mux()
    instance = Instance(NAME)
    loop_pid_file = instance.loop_pid_file

    print("=== setup ===")
    check("stub copilot on PATH", shutil.which("copilot") is not None)
    if not check("multiplexer available", mux.available(), mux.binary):
        print("\n  This harness drives a real multiplexer. Install tmux "
              "(or the configured backend) and run it again.")
        shutil.rmtree(tmp, ignore_errors=True)
        return 1
    check("session name is free", not mux.has_session(instance.session),
          instance.session)

    try:
        print("=== start loop ===")
        _spawn_background_loop(instance, ["--agent", "test:agent"],
                               is_fresh=True, adopt=False, cwd=str(project))

        session = wait_for(lambda: mux.has_session(instance.session) or None)
        check("session came up", bool(session))
        old_loop_pid = wait_for(lambda: read_pid(loop_pid_file))
        check("supervisor recorded a pid", old_loop_pid is not None,
              str(old_loop_pid))
        if not session or old_loop_pid is None:
            return 1

        old_pane_pid = wait_for(lambda: mux.pane_pid(instance.session))
        check("pane has a pid", old_pane_pid is not None, str(old_pane_pid))

        args_file = instance.loop_args_file
        # probe-ok: both probes are the check itself -- this harness reports to
        # a human watching it, so a wrong False fails the check loudly and a
        # raise ends the run with a traceback in front of the same person.
        # Neither failure mode is silent, which is all this needs.
        check("loop args recorded", args_file.exists(),
              args_file.read_text(encoding="utf-8") if args_file.exists() else "")

        print("=== restart-loop ===")
        rc = restart_loop(NAME)
        check("restart-loop returned 0", rc == 0, str(rc))

        print("=== the property under test ===")
        check("session still exists", mux.has_session(instance.session))
        new_pane_pid = mux.pane_pid(instance.session)
        check("pane pid UNCHANGED (session survived)",
              new_pane_pid == old_pane_pid, f"{old_pane_pid} -> {new_pane_pid}")

        new_loop_pid = wait_for(lambda: _changed_pid(loop_pid_file, old_loop_pid))
        check("supervisor pid CHANGED (new code loaded)",
              new_loop_pid is not None and new_loop_pid != old_loop_pid,
              f"{old_loop_pid} -> {new_loop_pid}")
        check("old supervisor is gone", not pid_alive(old_loop_pid),
              str(old_loop_pid))

        print("=== adopted supervisor still supervises ===")
        # Kill the pane's program: a live supervisor must notice and relaunch.
        mux.kill_session(instance.session)
        relaunched = wait_for(lambda: mux.has_session(instance.session) or None,
                              timeout=90)
        check("adopted supervisor relaunched a dead session", bool(relaunched))
        return 1 if failures else 0
    finally:
        print("=== cleanup ===")
        try:
            _request_supervisor_stop(instance)
        except Exception as exc:                                # noqa: BLE001
            print(f"  supervisor stop request failed ({exc})")
        for _ in range(20):
            if not mux.has_session(instance.session):
                break
            time.sleep(0.5)
        # Only ever this run's own session: the name carries this pid.
        if mux.has_session(instance.session):
            mux.kill_session(instance.session)
        pid = read_pid(loop_pid_file)
        if pid and pid_alive(pid):
            kill_pid(pid)
        shutil.rmtree(tmp, ignore_errors=True)
        print(f"  cleaned up {tmp}")


if __name__ == "__main__":
    rc = main()
    print(f"\n=== summary ===\n  failures: {len(failures)}"
          + ("" if not failures else "\n  " + "\n  ".join(failures)))
    sys.exit(rc)
