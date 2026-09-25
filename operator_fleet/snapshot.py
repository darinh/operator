"""What a supervised instance looks like right now, and the board that prints it.

Describing and supervising stay apart: a status read must never be able to
change what it is reporting on, and nothing below writes anything.

The board lived nowhere for a while, and `operator list` printed a seat name
per row instead. That is not a cosmetic gap. The one CAUTION the launch
preamble ever prints tells an agent that ``operator list`` "names the changed
files and every instance affected", and `loop_record_facts` has always
returned those paths beside the verdict. There was no board to print them and
`instance_snapshot` dropped them from the row, so the agent that went and
checked, because the preamble told it to, learned strictly less than the
agent that did not bother.

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
from presence import path_present
from config import (CODE_MISMATCH, CODE_STALE, CODE_UNKNOWN, CODE_UNRECORDED,
                    MUX, OPERATOR_HOME)
from instance import Instance
from provenance import loop_record_facts
from supervisor_records import _running_loop_identity
import supervisor_control

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
        # The paths behind the verdict, carried rather than recomputed. The
        # board is told to name them and cannot name what the row drops, and
        # re-reading them at print time would compare disk against a disk
        # that has moved on since the verdict was decided.
        "loop_changed": record["changed"],
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


#: Verdicts a restart is known to mend. ``unknown`` is deliberately absent:
#: nobody could compare that supervisor at all, so a restart is a guess, and
#: offering a remedy for a state nobody has diagnosed spends the reader's
#: trust on the one row that least deserves it. It still gets a notice below,
#: because saying nothing is the failure this whole instrument exists for.
REMEDIABLE = (CODE_STALE, CODE_UNRECORDED, CODE_MISMATCH)

#: What each verdict costs the reader, in the row's own words. ``current`` is
#: absent rather than empty: the overwhelmingly common case stays silent, for
#: the same reason `preamble._code_state_notice` gives about attaching a
#: caveat to every session. A verdict missing from here renders an ordinary
#: row, which `tests/test_snapshot.py` fails on rather than tolerates.
_CODE_NOTICE = {
    CODE_STALE: "OUT-OF-DATE code, changed since it started:",
    CODE_UNRECORDED: "recorded nothing about the code it imported",
    CODE_MISMATCH: "startup record belongs to a different process",
    CODE_UNKNOWN: "startup record could not be compared against the tree",
}


def _instance_summary(snap: dict) -> str:
    """One row: the seat, and what its supervisor cannot show about itself.

    Every notice is gated on a live loop pid. A seat whose supervisor was
    stopped has imported nothing that could be behind disk, so a staleness
    verdict about it describes nothing -- and the gate is load-bearing in
    both directions, which is what `tests/test_loop_pid_identity.py` is for:
    a recycled pid read as live switches four notices on for a supervisor
    that cannot be described.
    """
    row = f"  {snap['name']}"
    if not snap.get("loop_pid"):
        return row
    notice = _CODE_NOTICE.get(snap.get("loop_code"), "")
    return f"{row}   {notice}" if notice else row


def list_instances() -> int:
    """Every running seat, and every supervisor that cannot show it is current.

    The remedy is offered once for the group and never once per instance, and
    that is the incident this function was rebuilt around rather than a
    preference about output. An operator change makes every supervisor on the
    machine stale at the same instant -- each imported its code once, at
    startup -- so the sweep is the normal case and the per-instance restart is
    the exception. This listing once named eight stale supervisors and printed
    eight commands to type, and a remedy applied by hand once per instance is
    a remedy applied to some of them.

    ``active_instances`` is reached through its module rather than bound at
    import. The binding a ``from`` import makes is resolved once, before any
    test or caller can substitute the roster, and the two `test_entry.py`
    cases that drive `operator list` caught exactly that: they patch
    `supervisor_control`, the function they are grading read a copy taken at
    import time, and the listing reported an empty machine.
    """
    found = supervisor_control.active_instances()
    if not found:
        print("No running seats.")
        return 0
    behind = 0
    for inst in found:
        snap = instance_snapshot(inst)
        print(_instance_summary(snap))
        if not snap.get("loop_pid"):
            continue
        for path in snap.get("loop_changed") or ():
            print(f"      {path}")
        if snap.get("loop_code") in REMEDIABLE:
            behind += 1
    if behind:
        subject = ("supervisor cannot show it is" if behind == 1
                   else "supervisors cannot show they are")
        print(f"\n{behind} of {len(found)} {subject} running the operator "
              f"code that is on disk now.")
        print("Replace them all with:\n  operator restart-loop --all")
    return 0
