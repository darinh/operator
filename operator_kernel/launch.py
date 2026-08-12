"""Extracted from copilot_operator.py. See docs/spike-extraction.md."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
import uuid
import hashlib
import sqlite3
import signal
import contextlib
import ntpath
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from config import (COPILOT_LOG_DIR, METRICS_DB, MUX, RESTART_DIR, SESSION_ARG_RE)
from presence import path_present
from instance import Instance
from probes import die, log, remove_file

def write_launch_spec(instance: Instance, argv: list[str], cwd: Path,
                      session_num: int) -> Path:
    spec = {
        "instance": instance.id,
        "display_name": instance.display_name,
        "argv": argv,
        "cwd": str(cwd),
        "session_num": session_num,
        "state_dir": str(RESTART_DIR),
        "metrics_db": str(METRICS_DB),
        "copilot_log_dir": str(COPILOT_LOG_DIR),
    }
    tmp = instance.spec_file.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(spec, indent=2), encoding="utf-8")
    os.replace(tmp, instance.spec_file)
    return instance.spec_file


def runner_argv(spec_path: Path) -> list[str]:
    """Command the multiplexer runs inside the pane.

    Passed as an explicit argv list (never a shell string) so arguments keep
    their exact spelling regardless of platform quoting rules.
    """
    runner = Path(__file__).resolve().parent / "operator_runner.py"
    return [sys.executable, str(runner), str(spec_path)]


def copilot_executable() -> str | None:
    return shutil.which("copilot")


def _ensure_usage_logging(argv: list[str]) -> list[str]:
    """Ensure Copilot logs the data the metrics pipeline depends on.

    Since the move to AI credits, usage is reported in the chat-completion
    response bodies (``copilot_usage.total_nano_aiu``), and those bodies are
    only written at debug log level. At the default level the process log
    contains no usage data at all, so metrics would silently be empty.

    Set ``COPILOT_OPERATOR_NO_DEBUG_LOG=1`` to opt out — sessions will run with
    smaller logs but will record no usage.
    """
    if os.environ.get("COPILOT_OPERATOR_NO_DEBUG_LOG"):
        return argv
    if any(a == "--log-level" or a.startswith("--log-level=") for a in argv):
        return argv
    return [*argv, "--log-level", "debug"]


def start_session(instance: Instance, copilot_args: list[str], session_num: int,
                  remain_on_exit: bool, preamble: str = "") -> None:
    cwd = Path.cwd()
    exe = copilot_executable()
    if not exe:
        die("GitHub Copilot CLI ('copilot') was not found on PATH.\n"
            "  Install it: https://docs.github.com/en/copilot/how-tos/copilot-cli")

    argv = [exe, *copilot_args]
    if preamble:
        argv += ["-i", preamble]
    argv = _ensure_usage_logging(argv)

    remove_file(instance.restart_marker)
    # `remove_file` already logs the failure; what is recorded here is the
    # consequence — an exit code surviving this point belongs to the session
    # that just ended, not the one about to start.
    instance.exit_file_cleared = remove_file(instance.exit_file)
    remove_file(instance.session_file)

    spec = write_launch_spec(instance, argv, cwd, session_num)

    log(f"Session #{session_num}: launching copilot")
    log(f"  Work dir: {cwd}")

    if MUX.has_session(instance.session):
        MUX.kill_session(instance.session)
        time.sleep(0.5)

    MUX.new_session(instance.session, str(cwd), runner_argv(spec))
    MUX.set_remain_on_exit(instance.session, remain_on_exit)

    instance.claim(uuid.uuid4().hex)

    # Wait briefly for the runner to publish Copilot's real PID.
    for _ in range(30):
        if instance.copilot_pid() is not None:
            break
        if path_present(instance.exit_file) is True:
            break
        time.sleep(0.2)

    pid = instance.copilot_pid()
    log(f"  Session #{session_num} running (copilot pid={pid or 'pending'}) — "
        f"attach with: operator join {instance.display_name}")


# ── argument helpers ────────────────────────────────────────────
def extract_agent_from_args(args: list[str]) -> str:
    for i, arg in enumerate(args):
        if arg.startswith("--agent="):
            return arg.split("=", 1)[1]
        if arg == "--agent" and i + 1 < len(args):
            return args[i + 1]
    return "anvil:anvil"


def args_have_explicit_session(args: list[str]) -> bool:
    return any(SESSION_ARG_RE.match(a) for a in args)


def has_agent_flag(args: list[str]) -> bool:
    return any(a == "--agent" or a.startswith("--agent=") for a in args)


def with_experimental(defaults: list[str]) -> list[str]:
    """Append `--experimental` to the operator's injected defaults.

    Runtime extensions -- `checkout-guard` among them -- load ONLY when the
    CLI is in experimental mode, and the CLI persists the last spelling it was
    given into `~/.copilot/settings.json`. So the flag is sticky global state
    that any other session, on any project, can flip; and when it is off,
    every extension silently does not load. There is no error and no missing
    output, because an extension that never loaded cannot report its own
    absence. That was measured on this machine: agent sessions ran for over an
    hour with no checkout-guard at all, in the shared primary checkout it
    exists to protect, and nothing inside those sessions could have told.

    Passing it explicitly on every launch is what makes the guard's silence
    mean "scanned and found nothing" rather than "was never there".

    It is added UNCONDITIONALLY, and callers must place the result BEFORE the
    user's own arguments. A user who really wants `--no-experimental` still
    gets it, because the CLI resolves conflicting spellings last-wins -- both
    orders were measured against CLI 1.0.77:

        copilot --experimental --no-experimental ...  -> experimental: false
        copilot --no-experimental --experimental ...  -> experimental: true

    Deciding by *inspecting* the user's arguments instead is what the earlier
    version of this function did, and it was wrong: it could not tell a flag
    from a value, so `-p --no-experimental` -- a prompt that merely looks like
    a ruling -- suppressed the injected flag and put the session straight back
    into the silent, guardless state this exists to prevent. Any such check
    needs a list of which options take values, and that list goes stale every
    time the CLI grows one. Ordering needs no list.
    """
    return [*defaults, "--experimental"]


def handle_existing_session(instance: Instance) -> None:
    if not MUX.has_session(instance.session):
        return
    owner = instance.ownership()
    if owner is None:
        die(f"A session named '{instance.session}' already exists but was not created "
            f"by the operator. Refusing to touch it.\n"
            f"  Choose another name with --name, or stop that session yourself.")
    print(f"Session '{instance.display_name}' is already running.", file=sys.stderr)
    try:
        answer = input("Stop it and start a new one? [y/N] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        answer = ""
    if answer in ("y", "yes"):
        log(f"Stopping existing session '{instance.display_name}' at user request")
        MUX.kill_session(instance.session)
        instance.cleanup_files()
        time.sleep(1)
    else:
        print("Aborted.", file=sys.stderr)
        sys.exit(1)
