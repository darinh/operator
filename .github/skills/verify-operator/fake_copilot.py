"""A stand-in for the GitHub Copilot CLI, so a run can drive operator for free.

`control_operator.py up` installs this file as the `copilot` console script in
the run's venv. `operator start` then finds it with `shutil.which("copilot")`,
and the runner launches it in the operator's pane exactly as it would launch
Copilot. It is a pip launcher, not a `.cmd`, because cmd.exe re-parses argv,
expands `%VAR%` and splits at `&`, which changes the prompt it was handed.

At startup it:

1. Puts `FAKE_PATH_FIRST` at the front of PATH. A psmux pane gets the registry
   PATH, not the client's, so without this, `operator` inside the pane is
   whatever is installed globally rather than the checkout under test.
2. Writes `<COPILOT_LOG_DIR>/process-<ms>-<pid>.log` holding a session id, the
   way Copilot does. The runner reads the id from it, and the supervisor types
   mail into a session only once it has one.
3. Reads its operator name from the preamble's `You are operator NAME (ID).`
4. Runs the steps in `<run>/artifacts/scripts/NAME.sN.json` for session N, or
   else `NAME.json`. `control_operator.py agent` writes those files.
5. Reads stdin until it is killed or a line is `/exit`. It appends each line
   to `stdin.log` and runs every `on` step whose text the line contains.

It writes everything under `<run>/artifacts/agents/NAME/`: `starts.log`,
`prompt-N.txt`, `commands.log` and `stdin.log`. Those survive `down`.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

BANNER = "fake copilot for verify-operator"

#: Every step is a JSON object with exactly one of these keys.
STEPS = {
    "op": "run `operator` with this list of arguments",
    "sleep": "wait this many seconds",
    "cd": "change directory, relative to the current one",
    "on": "when a stdin line contains this text, run the steps under `do`",
    "exit": "end the session with this exit code",
}

IDENTITY = re.compile(r"You are operator (.+?) \(([^()\s]+)\)\.")


def validate_steps(steps) -> None:
    """Raise ValueError naming the first step this fake cannot run."""
    if not isinstance(steps, list):
        raise ValueError("steps must be a JSON list")
    for index, step in enumerate(steps):
        where = f"step {index + 1}"
        if not isinstance(step, dict):
            raise ValueError(f"{where} is not a JSON object: {step!r}")
        keys = set(step) - {"do"}
        if len(keys) != 1 or not keys <= set(STEPS):
            raise ValueError(f"{where} needs exactly one of {sorted(STEPS)}: {step!r}")
        (kind,) = keys
        value = step[kind]
        if "do" in step and kind != "on":
            raise ValueError(f"{where}: only an `on` step takes `do`")
        if kind == "op" and not (isinstance(value, list)
                                 and all(isinstance(a, str) for a in value)):
            raise ValueError(f"{where}: `op` takes a list of strings")
        if kind == "sleep" and not isinstance(value, (int, float)):
            raise ValueError(f"{where}: `sleep` takes a number of seconds")
        if kind == "cd" and not isinstance(value, str):
            raise ValueError(f"{where}: `cd` takes a path")
        if kind == "exit" and not isinstance(value, int):
            raise ValueError(f"{where}: `exit` takes an integer")
        if kind == "on":
            if not isinstance(value, str) or not value:
                raise ValueError(f"{where}: `on` takes the text to watch for")
            validate_steps(step.get("do", []))


def identity(prompt: str) -> tuple[str, str]:
    """The operator's name and id from the preamble, or ``("unnamed", "")``."""
    found = IDENTITY.search(prompt)
    return (found.group(1), found.group(2)) if found else ("unnamed", "")


def prompt_of(argv: list[str]) -> str:
    for index, arg in enumerate(argv):
        if arg in ("-i", "-p") and index + 1 < len(argv):
            return argv[index + 1]
    return ""


def script_for(scripts: Path, name: str, session: int) -> Path | None:
    for candidate in (scripts / f"{name}.s{session}.json", scripts / f"{name}.json"):
        if candidate.is_file():
            return candidate
    return None


def write_process_log(log_dir: Path, session_id: str, pid: int, now_ms: int) -> Path:
    """The file the runner pins to this launch and reads the session id from."""
    log_dir.mkdir(parents=True, exist_ok=True)
    path = log_dir / f"process-{now_ms}-{pid}.log"
    stamp = _now()
    path.write_text(f"{stamp} [INFO] {BANNER}\n"
                    f"{stamp} [INFO] {json.dumps({'session_id': session_id})}\n",
                    encoding="utf-8")
    return path


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


class Agent:
    def __init__(self, run: Path, name: str, op_id: str):
        self.name = name
        self.op_id = op_id
        self.logs = run / "artifacts" / "agents" / name
        self.logs.mkdir(parents=True, exist_ok=True)
        self.handlers: list[tuple[str, list]] = []

    def log(self, filename: str, text: str) -> None:
        with open(self.logs / filename, "a", encoding="utf-8") as fh:
            fh.write(text if text.endswith("\n") else text + "\n")

    def say(self, text: str) -> None:
        print(text, flush=True)

    def run(self, steps: list) -> None:
        for step in steps:
            self.step(step)

    def step(self, step: dict) -> None:
        if "op" in step:
            self.operator(step["op"])
        elif "sleep" in step:
            time.sleep(step["sleep"])
        elif "cd" in step:
            os.chdir(step["cd"])
            self.log("commands.log", f"[{_now()}] cd {os.getcwd()}")
        elif "on" in step:
            self.handlers.append((step["on"], step.get("do", [])))
        elif "exit" in step:
            self.log("commands.log", f"[{_now()}] exit {step['exit']}")
            sys.exit(step["exit"])

    def operator(self, args: list[str]) -> None:
        exe = shutil.which("operator")
        self.say(f"$ operator {' '.join(args)}")
        if exe is None:
            self.log("commands.log", f"[{_now()}] $ operator {args!r}\noperator not on PATH")
            return
        started, clock = _now(), time.monotonic()
        proc = subprocess.run([exe, *args], capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=180,
                              stdin=subprocess.DEVNULL)
        took = time.monotonic() - clock
        out = (proc.stdout + proc.stderr).rstrip()
        self.log("commands.log", f"[{started}] cwd={os.getcwd()}\n$ operator "
                 f"{' '.join(args)}\nexit {proc.returncode} after {took:.1f}s\n{out}\n")
        self.say(f"{out}\nexit {proc.returncode}" if out else f"exit {proc.returncode}")

    def listen(self) -> None:
        while True:
            line = sys.stdin.readline()
            if not line:
                # Copilot does not end a session at EOF either.
                while True:
                    time.sleep(3600)
            line = line.rstrip("\r\n")
            self.log("stdin.log", f"[{_now()}] {line}")
            self.say(f"<< {line}")
            if line.strip() == "/exit":
                # What the kernel types to end a session, and Copilot obeys it.
                sys.exit(0)
            for text, steps in list(self.handlers):
                if text in line:
                    self.run(steps)


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if "--version" in argv or "--help" in argv:
        print(BANNER)
        return 0
    run = os.environ.get("FAKE_RUN")
    if not run:
        print("fake copilot: FAKE_RUN is not set. It only runs inside a "
              "verify-operator run.", file=sys.stderr)
        return 2
    run_dir = Path(run)
    first = os.environ.get("FAKE_PATH_FIRST")
    if first:
        os.environ["PATH"] = first + os.pathsep + os.environ.get("PATH", "")

    prompt = prompt_of(argv)
    name, op_id = identity(prompt)
    agent = Agent(run_dir, name, op_id)
    starts = agent.logs / "starts.log"
    session = 1 + (len(starts.read_text(encoding="utf-8").splitlines())
                   if starts.exists() else 0)
    session_id = str(uuid.uuid4())
    log_dir = os.environ.get("COPILOT_LOG_DIR")
    if log_dir:
        write_process_log(Path(log_dir), session_id, os.getpid(),
                          int(time.time() * 1000))
    script = script_for(run_dir / "artifacts" / "scripts", name, session)
    agent.log("starts.log", f"[{_now()}] session={session} pid={os.getpid()} "
              f"id={op_id} session_id={session_id} cwd={os.getcwd()} "
              f"operator={shutil.which('operator')} "
              f"script={script.name if script else 'none'}")
    agent.log(f"prompt-{session}.txt", prompt)
    agent.say(f"{BANNER}: operator {name} ({op_id}), session {session}, "
              f"script {script.name if script else 'none'}")

    steps = json.loads(script.read_text(encoding="utf-8")) if script else []
    agent.run(steps)
    agent.say("steps done, listening")
    agent.listen()
    return 0


if __name__ == "__main__":
    sys.exit(main())
