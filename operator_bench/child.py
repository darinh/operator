"""Child process: env first, then the real supervisor against a scripted seat."""
from __future__ import annotations

import inspect
import json
import os
import sys
import time
from collections import defaultdict
from pathlib import Path

from operator_bench.world import git

_real_time = time

#: Kernel modules that sleep or read the clock. Imported before the rebind so
#: the scan cannot miss one that nothing has pulled in yet.
_CLOCK_USERS = (
    "launch", "mux", "runner", "session_state", "supervisor",
    "supervisor_control", "supervisor_records", "process_identity",
    "breakers", "probes", "exits", "claims", "evidence",
)


class ClockExhausted(RuntimeError):
    pass


def _install_clock(clock) -> None:
    """Rebind the name `time` inside kernel modules only.

    Patching `time.sleep` on the module itself would also reach `subprocess`,
    whose POSIX `Popen._wait` busy-waits on `time.sleep` while Windows blocks
    in `WaitForSingleObject`. That difference silently burned the whole clock
    budget on Linux and nothing on Windows.
    """
    import importlib
    for name in _CLOCK_USERS:
        try:
            importlib.import_module(name)
        except ImportError:
            continue
    roots = _source_roots()
    reached = []
    for name, mod in list(sys.modules.items()):
        if mod is None or not _under(getattr(mod, "__file__", None), roots):
            continue
        if isinstance(getattr(mod, "time", None), type(_real_time)):
            mod.time = clock
            reached.append(name)
    if "supervisor" not in reached:
        raise RuntimeError(f"clock did not reach supervisor, only {reached}")


def _source_roots() -> tuple[Path, ...]:
    root = Path(__file__).resolve().parent.parent
    return (root / "operator_kernel", root / "operator_fleet")


def _under(path: str | None, roots: tuple[Path, ...]) -> bool:
    if not path:
        return False
    try:
        resolved = Path(path).resolve()
    except OSError:
        return False
    return any(resolved.is_relative_to(r) for r in roots)


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if not argv:
        sys.stderr.write("usage: python -m operator_bench.child PROGRAM.json\n")
        return 2
    home = os.environ.get("COPILOT_OPERATOR_HOME", "")
    if not home:
        sys.stderr.write("COPILOT_OPERATOR_HOME must be set before import\n")
        return 2
    program = json.loads(Path(argv[0]).read_text(encoding="utf-8"))
    return _run(Path(home), program)


def _run(home: Path, program: dict) -> int:
    import config
    if Path(config.OPERATOR_HOME).resolve() != home.resolve():
        sys.stderr.write("kernel OPERATOR_HOME is not the sandbox home\n")
        return 2
    from instance import Instance
    from mux import Mux
    from supervisor import run_loop_mode

    clock = _Clock(program.get("max_virtual_seconds", 20_000),
                   program.get("max_sleeps", 200_000))
    clock.poll_interval = float(config.POLL_INTERVAL)
    _install_clock(clock)
    seat = _Seat(home, program, clock)
    Mux._run = seat.run
    result = home / "bench-result.json"
    rc = 1
    err = None
    try:
        inst = Instance(program.get("instance", "bench"))
        rc = run_loop_mode(
            inst, list(program.get("user_args", ["--agent", "bench:seat"])),
            is_fresh=True)
    except ClockExhausted as exc:
        err = str(exc)
        rc = 2
    except Exception as exc:
        err = f"{type(exc).__name__}: {exc}"
        rc = 1
    result.write_text(json.dumps({
        "exit_code": rc,
        "error": err,
        "sleep_report": clock.report(),
        "sleeps": clock.sleeps,
        "virtual_seconds": clock.t,
        "polls": clock.polls,
        "launch_polls": seat.launch_polls,
    }, indent=2), encoding="utf-8")
    return rc


class _Clock:
    def __init__(self, max_s: float, max_n: int) -> None:
        self.t = 0.0
        self.sleeps = 0
        self.max_s = float(max_s)
        self.max_n = int(max_n)
        self.ledger: list[tuple[float, float, str, str]] = []
        self._on_tick = lambda: None
        self.polls = 0
        self.poll_interval = 10.0
        self._sleep_frame = None

    def now(self) -> float:
        self._on_tick()
        return self.t

    def time(self) -> float:
        return self.now()

    def monotonic(self) -> float:
        return self.now()

    def perf_counter(self) -> float:
        return self.now()

    def __getattr__(self, name: str):
        return getattr(_real_time, name)

    def sleep(self, seconds: float) -> None:
        seconds = max(0.0, float(seconds))
        self.sleeps += 1
        frame = inspect.currentframe()
        caller = frame.f_back if frame is not None else None
        mod = caller.f_globals.get("__name__", "?") if caller else "?"
        func = caller.f_code.co_name if caller else "?"
        if self.sleeps > self.max_n or self.t + seconds > self.max_s:
            raise ClockExhausted(
                f"clock exhausted in {mod}.{func} at {self.t}s "
                f"after {self.sleeps} sleeps")
        self.ledger.append((self.t, seconds, mod, func))
        if caller and func == "_sleep":
            fid = id(caller)
            if fid != self._sleep_frame:
                self._sleep_frame = fid
                total = caller.f_locals.get("total")
                if total == self.poll_interval:
                    self.polls += 1
        self.t += seconds
        self._on_tick()

    def report(self) -> list[list]:
        totals: dict[str, list[float]] = defaultdict(lambda: [0.0, 0.0])
        for _at, seconds, mod, func in self.ledger:
            key = f"{mod}.{func}"
            totals[key][0] += 1
            totals[key][1] += seconds
        rows = [[k, int(c), t] for k, (c, t) in totals.items()]
        rows.sort(key=lambda r: r[2], reverse=True)
        return rows


class _Seat:
    def __init__(self, home: Path, program: dict, clock: _Clock) -> None:
        self.home = home
        self.program = program
        self.clock = clock
        self.sessions: dict[str, dict] = {}
        self.index = 0
        self.pending: list[tuple[str, dict, float]] = []
        self.launch_polls: list[int] = []
        clock._on_tick = self._fire

    def _paths(self):
        from config import RESTART_DIR
        from mux import safe_instance_id
        iid = safe_instance_id(self.program.get("instance", "bench"))
        return (RESTART_DIR / f"{iid}.stopreq",
                RESTART_DIR / iid,
                RESTART_DIR / f"{iid}.exit")

    def _fire(self) -> None:
        due = []
        keep = []
        for name, spec, deadline in self.pending:
            if self.clock.t >= deadline:
                due.append((name, spec))
            else:
                keep.append((name, spec, deadline))
        self.pending = keep
        for name, spec in due:
            self._end(name, spec)

    def _end(self, name: str, spec: dict) -> None:
        stop, restart, exit_file = self._paths()
        ending = spec.get("ending", "handoff")
        session = self.sessions.get(name)
        if ending == "stop":
            stop.touch()
        elif ending == "handoff":
            restart.touch()
        elif ending == "exit":
            exit_file.write_text("0", encoding="ascii")
            if session is not None:
                session["dead"] = True
        elif ending == "unaccounted":
            if session is not None:
                session["dead"] = True
        else:
            raise AssertionError(f"unmodelled ending {ending!r}")

    def _effect(self, spec: dict, n: int) -> None:
        kind = spec.get("effect", "silence")
        cwd = Path.cwd()
        if kind == "silence" or kind == "launch_fail":
            return
        if kind == "work":
            path = cwd / "src.txt"
            path.write_text(f"work {n}\n", encoding="utf-8")
            git(cwd, "add", "-A")
            git(cwd, "commit", "-m", f"work {n}")
            return
        if kind == "busywork":
            junk = cwd / "junk.bench"
            junk.write_bytes(bytes((n & 255, n >> 8 & 255)) + os.urandom(6))
            return
        raise AssertionError(f"unmodelled effect {kind!r}")

    def run(self, *args: str, capture: bool = True) -> tuple[str, str, int]:
        self._fire()
        verb = args[0] if args else ""
        if verb == "-V":
            return "benchmux 0.0", "", 0
        if verb == "has-session":
            return "", "", 0 if args[2] in self.sessions else 1
        if verb == "list-sessions":
            return "\n".join(self.sessions), "", 0
        if verb == "new-session":
            return self._new(args)
        if verb == "kill-session":
            name = args[2]
            if name not in self.sessions:
                return "", f"can't find session: {name}", 1
            del self.sessions[name]
            return "", "", 0
        if verb == "set-option":
            name = args[2]
            if name not in self.sessions:
                return "", f"can't find session: {name}", 1
            self.sessions[name]["remain_on_exit"] = args[4] == "on"
            return "", "", 0
        if verb == "send-keys":
            return self._keys(args)
        if verb == "display-message":
            return self._display(args)
        if verb == "attach":
            return "", "", 0 if args[2] in self.sessions else 1
        raise AssertionError(
            f"unmodelled mux verb {verb!r} (args: {args!r})")

    def _new(self, args: tuple[str, ...]) -> tuple[str, str, int]:
        scripts = self.program.get("sessions", [])
        if self.index >= len(scripts):
            stop, _restart, _exit = self._paths()
            stop.touch()
            return "", "no more scripted sessions", 1
        spec = scripts[self.index]
        if spec.get("effect") == "launch_fail" or spec.get("ending") == "launch_fail":
            return "", "scripted launch failure", 1
        self.index += 1
        self.launch_polls.append(self.clock.polls)
        name = args[3]
        self.sessions[name] = {
            "cwd": args[5], "argv": list(args[7:]),
            "remain_on_exit": False, "dead": False,
        }
        self._effect(spec, self.index)
        deadline = self.clock.t + float(spec.get("duration_s", 0))
        if float(spec.get("duration_s", 0)) <= 0:
            self._end(name, spec)
        else:
            self.pending.append((name, spec, deadline))
        return "", "", 0

    def _keys(self, args: tuple[str, ...]) -> tuple[str, str, int]:
        name = args[2]
        if name not in self.sessions:
            return "", f"can't find session: {name}", 1
        payload = list(args[3:])
        if payload[:1] == ["-l"]:
            payload = payload[1:2]
        if "/exit" in payload:
            self.sessions[name]["dead"] = True
        return "", "", 0

    def _display(self, args: tuple[str, ...]) -> tuple[str, str, int]:
        name = args[2]
        session = self.sessions.get(name)
        if session is None:
            return "", f"can't find session: {name}", 1
        fmt = args[4]
        if fmt == "#{pane_dead}":
            return "1" if session["dead"] else "0", "", 0
        if fmt == "#{pane_pid}":
            return "0", "", 0
        if fmt == "#{pane_current_path}":
            return session["cwd"], "", 0
        raise AssertionError(f"unmodelled display format {fmt!r}")


if __name__ == "__main__":
    raise SystemExit(main())
