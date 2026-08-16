"""What a supervised instance looks like right now.

The board reads this. It is deliberately separate from the loop: a status
read must never be able to change what it is reporting on.

**This is why it is not in the kernel.** Describing a fleet is not supervising
one, and the arrow proves it: no kernel module imports this file, while this
file imports five of them. A leaf that only ever points inward is not part of
what it points at. `docs/plan.md` already said the fleet host belongs outside
`operator_kernel/`; the board's read of a single instance belongs there on the
same argument, and it was inside only because the module both were extracted
from made no distinction.

The move was not free of a reason either: the kernel stood at 4,091 of 4,100
code lines and exactly 9,000 of 9,000 total, so the next line of anything
failed `test_kernel_boundary`. The budget names the cut to make when that
happens, and the rule it encodes is *cut before you raise*. This is a cut --
which only counts if the lines are not simply free on this side of the line, so
`tests/test_fleet_boundary.py` charges for them here too.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from presence import path_present
import instance
from config import MUX, OPERATOR_HOME
from instance import Instance
from provenance import loop_record_facts
from supervisor_records import _running_loop_identity

TABS_FILE = OPERATOR_HOME / "tabs.json"


# ── tab registry ────────────────────────────────────────────────
# Windows Terminal (and most terminal emulators) expose no API to list their
# own tabs, so the operator keeps its own record of which named instances were
# started from a terminal tab, in which directory, and with which arguments.
# After a reboot or crash every process is gone, but this file survives, and
# `operator restore` replays each entry in a fresh tab — the existing
# auto-continue/--resume logic then picks the Copilot session back up.
def read_tabs() -> dict | None:
    """The tab registry, or None when it exists but could not be read.

    The distinction matters because the registry is rewritten whole. Treating
    an unreadable file as an empty one would let the next ``register_tab``
    replace every other tab's restore record with a single entry.
    """
    if path_present(TABS_FILE) is False:
        return {}
    try:
        data = json.loads(TABS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else {}


def load_tabs() -> dict[str, dict]:
    """The tab registry as far as it can be read; unreadable reads as empty.

    Only for callers that display or filter. Anything that writes the file
    back must use :func:`read_tabs` and refuse the write on None.
    """
    entries = read_tabs()
    return {} if entries is None else entries


def instance_snapshot(instance: Instance) -> dict:
    """Everything the browser needs in order to describe one instance.

    Reads only state that already exists on disk, so it is safe to call
    repeatedly — refreshing the view never disturbs a running session.
    """
    state = instance.load_state() or {}
    owner = instance.ownership() or {}
    try:
        session_num = int(state.get("SESSION_NUM", 0) or 0)
    except ValueError:
        session_num = 0
    spec: dict = {}
    try:
        loaded = json.loads(instance.spec_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        loaded = None
    if isinstance(loaded, dict):
        spec = loaded
    cwd = spec.get("cwd") or (load_tabs().get(instance.id) or {}).get("cwd") or ""
    session_live = MUX.available() and MUX.has_session(instance.session)
    # Read once and passed to both record readers: they check it against the
    # pid the record carries, so a supervisor whose record could not be
    # rewritten is not described by its predecessor's. The token comes back
    # with it so the record reader does not ask the OS who holds that pid a
    # second time -- one `ps` fork per instance on macOS, not two.
    loop_pid, live_start = _running_loop_identity(instance)
    # Read once. Asking the four readers separately costs four file reads and
    # four process-identity probes per instance, and on macOS/BSD each probe
    # is a `ps` subprocess with a ten-second timeout.
    record = loop_record_facts(instance, loop_pid, live_start)
    return {
        "instance": instance,
        "name": instance.display_name,
        "id": instance.id,
        "session_live": session_live,
        # Ownership gates every destructive action, so the browser has to
        # surface it: a same-named session we did not start is look-only.
        "owned": session_live and instance.owns_live_session(),
        "loop_pid": loop_pid,
        "loop_code": record["code"],
        "loop_started": record["started"] or "",
        "loop_adopted": record["adopted"],
        "loop_began_run": record["began_run"],
        "session_num": session_num,
        "run_started": state.get("RUN_STARTED", "") or owner.get("claimed_at", ""),
        "copilot_session_id": (state.get("COPILOT_SESSION_ID", "")
                               or instance.read_session_id()),
        "copilot_pid": instance.copilot_pid(),
        "cwd": cwd,
        "argv": list(spec.get("argv") or []),
    }
