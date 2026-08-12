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
from config import TOOLKIT_VERSION
import instance
import process_identity

from config import (CODE_CURRENT, CODE_MISMATCH, CODE_STALE, CODE_UNKNOWN, CODE_UNRECORDED, FILE_ABSENT, _RUNNING_CODE, _UNPROBED)
from presence import entry
from instance import Instance
from probes import log, utcnow

def _digest_file(path: Path) -> "str | _FileAbsent | None":
    """sha256 of a file's bytes; ``FILE_ABSENT`` if gone, ``None`` if unknown.

    Three answers, not two. A digest that quietly became a constant when the
    file could not be read would compare equal to itself forever and report
    code it never saw as unchanged -- which is the failure this whole
    fingerprint exists to make impossible, reproduced inside it.

    Absence is separated from unreadability because they support opposite
    conclusions: a module the supervisor loaded that is no longer on disk has
    *definitely* changed, while one behind a denied read has not been
    examined at all.
    """
    try:
        with open(path, "rb") as fh:
            return hashlib.sha256(fh.read()).hexdigest()
    except FileNotFoundError:
        return FILE_ABSENT
    except (OSError, ValueError):
        return None


def _loaded_operator_sources(modules: "dict | None" = None) -> list[Path]:
    """The operator's own ``.py`` files that *this process* imported.

    Deliberately not a glob of the directory. The question a staleness check
    has to answer is "has the code I am running changed", which is about the
    files this process loaded -- not about every file that happens to sit
    beside them. A glob would mark a supervisor stale because an unrelated
    tool in the same checkout was edited, and in a repository under active
    development that fires constantly. A notice that always fires is one
    nobody reads, which would leave the instrument no better off than the
    silence it replaced.

    ``modules`` exists so a test can supply the module table instead of
    mutating the real ``sys.modules``. Asserting the negative any other way
    means naming a file and hoping nothing imported it, and the first version
    of that test named a file that does not exist -- which no implementation
    can return, so it passed against a globbing one too.
    """
    here = Path(__file__).resolve().parent
    found: dict[str, Path] = {}
    for module in list((sys.modules if modules is None else modules).values()):
        origin = getattr(module, "__file__", None)
        if not origin:
            continue
        try:
            resolved = Path(origin).resolve()
        except (OSError, ValueError, RuntimeError):
            continue
        if resolved.suffix != ".py":
            continue
        try:
            if here not in resolved.parents:
                continue
        except (OSError, ValueError):
            continue
        found[str(resolved)] = resolved
    return [found[key] for key in sorted(found)]


def _combined_digest(entries: "list[dict]") -> str:
    """One short digest over a set of per-file digests.

    Unreadable and absent files contribute their *state* rather than being
    skipped, so two fingerprints cannot come out equal because a file
    dropped out of both.
    """
    hasher = hashlib.sha256()
    for entry in sorted(entries, key=lambda e: e.get("path") or ""):
        hasher.update((entry.get("path") or "").encode("utf-8", "replace"))
        hasher.update(b"\0")
        hasher.update(str(entry.get("sha256")).encode("utf-8", "replace"))
        hasher.update(b"\n")
    return hasher.hexdigest()[:16]


def running_code_fingerprint() -> dict:
    """Digest of the operator source this process is running. Computed once.

    The cache is the point, not an optimisation. This has to keep answering
    for the code the process *loaded*; recomputing it later would hash
    whatever is on disk by then and report a supervisor as running code it
    has never executed -- stating the confusion the fingerprint exists to
    end, in the fingerprint's own voice.

    Honest about its own resolution: the bytes are read from disk moments
    after import rather than captured by the import itself, so a file edited
    inside that window is recorded as the newer bytes. That is a millisecond
    at startup, and it is the direction that under-reports staleness rather
    than inventing it.
    """
    global _RUNNING_CODE
    if _RUNNING_CODE is None:
        entries = []
        for path in _loaded_operator_sources():
            digest = _digest_file(path)
            entries.append({
                "path": str(path),
                "sha256": None if digest is None
                          else ("absent" if digest is FILE_ABSENT else digest),
            })
        _RUNNING_CODE = {
            "version": TOOLKIT_VERSION,
            "digest": _combined_digest(entries),
            "files": entries,
        }
    return _RUNNING_CODE


def _save_loop_code(instance: Instance, adopted: bool = False,
                    began_run: bool = True) -> None:
    """Record which operator source this supervisor started with.

    ``adopted`` says whether this supervisor took over a session that was
    already running (`operator restart-loop`) rather than starting one of its
    own. ``began_run`` says whether it began the run at all, or inherited a
    ``RUN_STARTED`` written by a predecessor. Both are recorded because both
    are only knowable at startup, and because without them a deliberate
    supervisor handover is indistinguishable on disk from one caused by every
    process in the logon session being destroyed: they leave the same evidence, a
    supervisor younger than the run it is running.

    Losing this costs a staleness verdict, never the running session, so it
    warns and carries on for the same reason ``_save_loop_args`` does.
    """
    payload = dict(running_code_fingerprint())
    payload["pid"] = os.getpid()
    # A pid is not an identity. Windows recycles them aggressively, so a
    # supervisor whose own write failed can keep a predecessor's record whose
    # pid the OS has since handed to it -- and the pid check would then read
    # that record as its own. The start token is compared only for equality
    # and only against a token recorded for the same pid, which is exactly the
    # discrimination missing here; `operator_session` and `operator_work`
    # already use it for the same reason.
    payload["pid_start"] = process_identity.process_start_token(os.getpid())
    # And the start token is boot-relative on Linux -- `_linux_start_token` is
    # field 22 of /proc/<pid>/stat, in clock ticks *since boot*. Across a
    # reboot a replacement can therefore collide with its predecessor on both
    # pid and token, which is the one case the token alone cannot refute.
    # `same_boot` handles the tagged forms correctly: exact for Linux's boot
    # uuid, tolerant for the Windows/macOS instant, and "cannot tell" across
    # kinds -- so this only ever refutes on evidence.
    payload["boot"] = process_identity.boot_identity()
    payload["recorded"] = utcnow()
    payload["adopted"] = bool(adopted)
    payload["began_run"] = bool(began_run)
    tmp = instance.loop_code_file.with_suffix(".json.tmp")
    try:
        tmp.write_text(json.dumps(payload), encoding="utf-8")
        os.replace(tmp, instance.loop_code_file)
    except OSError as exc:
        log(f"  Warning: could not record the running operator code: {exc}")


def loop_code_state(instance: Instance,
                    loop_pid: "int | None" = None) -> "tuple[str, list[str]]":
    """Is the supervisor running the code that is on disk now?

    Returns ``(verdict, changed_paths)`` where verdict is ``CODE_CURRENT``,
    ``CODE_STALE``, ``CODE_UNRECORDED`` or ``CODE_UNKNOWN``.

    A supervisor imported its code at startup and keeps it for the whole run,
    so an operator fix is inert for every instance already running when it
    landed. That was not a hypothetical: the fix that made ``session_exit``
    record handoff endings landed at 19:36 on 2026-08-04, and every
    supervisor on the machine had started at 13:28 -- so the evidence kept
    producing pre-fix records, dated after the fix, with nothing in them
    saying so. Backlog 0001 tells its next reader to scope a re-measurement
    to records "at or after 2026-08-05", and that instruction was already
    false when it was written.

    One observed difference is enough to say stale, even when other files
    could not be read: staleness is established by a single changed file,
    whereas *currency* is a claim about all of them and so cannot survive a
    file nobody could examine.

    A record that is *observed absent* is a fourth answer, not the third one.
    The record is written by the same change that reads it, so a supervisor
    that is running and has left none started before that change existed --
    or could not write one, which ``_save_loop_code`` warns about and
    survives. Either way its verdict is unavailable until it restarts, and
    the remedy is the same as for a stale one. Collapsing that into "cannot
    tell" is what made this instrument silent for the entire population it
    was built for: measured 2026-08-05T11:35Z, every one of the six running
    supervisors predated the record, so all six read ``unknown``, ``operator
    ls`` said nothing, and the output was byte-identical to a machine on
    which every supervisor was current.

    ``loop_pid``, when given, must match the pid the record carries or the
    verdict is ``CODE_MISMATCH``. `_save_loop_code` tolerates a failed write,
    so a supervisor whose record could not be replaced keeps its
    predecessor's -- and comparing *that* record's digests against disk
    answers about a process that has gone. It can read ``stale`` and send a
    perfectly current supervisor to be restarted, or read ``current`` and
    clear one that is genuinely behind. Both are verdicts about the wrong
    process, which is not a weaker version of the right one.

    ``CODE_MISMATCH`` rather than ``CODE_UNKNOWN`` because `list_instances`
    prints nothing for ``unknown``, so answering that here would replace a
    wrong verdict with no verdict -- and this whole item exists because a row
    that says nothing is indistinguishable from a healthy one.
    """
    payload, unusable = _read_loop_record(instance)
    if payload is None:
        return unusable, []
    if not _record_describes(payload, loop_pid):
        return CODE_MISMATCH, []
    return _compare_recorded_files(payload.get("files"))


def _read_loop_record(instance: Instance) -> "tuple[dict | None, str]":
    """The supervisor's startup record, or why it could not be had.

    Returns ``(payload, "")`` when the record was read, and
    ``(None, verdict)`` otherwise, where the verdict distinguishes
    ``CODE_UNRECORDED`` -- observed absent -- from ``CODE_UNKNOWN``, which is
    "nobody could look". Keeping those apart is the whole reason this
    function exists rather than a ``try/except`` at each call site: collapsing
    them is the defect that made `operator ls` silent for the entire
    population it was built for, and a second reader that re-derived the
    distinction would be free to get it wrong again.

    Two questions are asked of this one record -- *which code did the
    supervisor load* (`loop_code_state`) and *when did it start*
    (`loop_started_at`) -- and they are printed on the same row, so they must
    not disagree about whether the record exists.
    """
    try:
        raw = instance.loop_code_file.read_text(encoding="utf-8")
    except (FileNotFoundError, NotADirectoryError):
        # Definite: nothing is there, and nothing can be under a path whose
        # parent is a file. Distinguished from the denial below because they
        # support different claims -- this one says the supervisor never
        # recorded, that one says nobody could look.
        return None, CODE_UNRECORDED
    except (OSError, ValueError):
        # Something is there and could not be read (a denial, a directory in
        # its place, bytes that are not UTF-8). "Cannot tell" is the only
        # honest answer, and it must not borrow the confidence of the branch
        # above.
        return None, CODE_UNKNOWN
    try:
        payload = json.loads(raw)
    except ValueError:
        return None, CODE_UNKNOWN
    if not isinstance(payload, dict):
        # Valid JSON that is not an object -- `null`, `[]`, a bare string.
        # `json.loads` raises nothing for these, so the guard above lets them
        # through and `.get` would raise AttributeError out of `operator ls`,
        # taking down the status command for every instance over one damaged
        # file belonging to one. A record we cannot read is the same answer as
        # a record that is not there.
        return None, CODE_UNKNOWN
    return payload, ""


def loop_record_facts(instance: Instance,
                      loop_pid: "int | None" = None,
                      live_start: object = _UNPROBED) -> dict:
    """Everything the record answers, read once.

    The four readers below each open the file and re-run `_record_describes`,
    which is four reads and four identity probes per instance. That was free
    on Windows (`process_start_token` measures 0.021 ms there) and is not
    elsewhere: on macOS and BSD `process_identity._ps_start_token` spawns
    ``ps`` with a ten-second timeout, so `operator list` would fork four
    subprocesses per instance and could block for a long time on a machine
    with several. Found by adversarial review, which is the second time in
    this change that a Windows-only measurement said "free" about something
    that is not -- the same shape the repository's `os.path` note warns about.

    The readers stay, because they are the honest unit of the question for
    every other caller and for the tests. This is the one path that asks all
    four at once.

    ``live_start`` carries a token the caller has already probed for the same
    pid, so `instance_snapshot` -- which has just asked `_running_loop_identity`
    who holds it -- does not pay for a second fork. Defaulting to ``None``
    would be a different claim (``None`` means "asked, and the OS would not
    say"), so callers with nothing to hand over pass :data:`_UNPROBED`, and
    that is what the default is.
    """
    payload, unusable = _read_loop_record(instance)
    if payload is None:
        return {"code": unusable, "changed": [], "started": None,
                "adopted": None, "began_run": None}
    if not _record_describes(payload, loop_pid, live_start):
        return {"code": CODE_MISMATCH, "changed": [], "started": None,
                "adopted": None, "began_run": None}
    verdict, changed = _compare_recorded_files(payload.get("files"))
    recorded = payload.get("recorded")
    adopted = payload.get("adopted")
    began = payload.get("began_run")
    return {
        "code": verdict,
        "changed": changed,
        "started": recorded if isinstance(recorded, str) and recorded else None,
        "adopted": adopted if isinstance(adopted, bool) else None,
        "began_run": began if isinstance(began, bool) else None,
    }


def _record_describes(payload: dict, loop_pid: "int | None",
                      live_start: object = _UNPROBED) -> bool:
    """Is this record the running supervisor's own, rather than a leftover?

    A caller that does not know which pid is running (``loop_pid`` is
    ``None``) gets ``True``: it asked a question about whatever record is
    there, and inventing a mismatch it cannot check would be its own kind of
    false answer.

    When a pid *is* supplied, an integer record pid equal to it is required.
    A record carrying no pid, or a non-integer one, cannot establish that it
    describes the live process, and "cannot establish" is a mismatch here
    rather than a pass -- there is no pre-pid schema to be lenient towards.
    `_save_loop_code` has stamped `pid` since the commit that introduced the
    record (7b5b58d, verified with `git log -S`), so a record without one was
    never written by any version of this code: it is damaged, and treating
    damage as agreement is exactly the leftover-record hole this closes.

    An equal pid is necessary and not sufficient, because a pid is not an
    identity: Windows recycles them, so a supervisor whose own write failed
    can inherit a predecessor's record bearing a pid the OS has since given to
    it. ``pid_start`` refutes that when both sides have one -- it is compared
    only for equality and only for the same pid, so a recycled pid carries a
    different token.

    The two ways it can be absent are deliberately *not* treated like the
    missing pid above, because unlike `pid`, `pid_start` has a real pre-stamp
    history: every supervisor running when it landed wrote a record without
    one. A record where the key is *absent*, or a live process whose token
    cannot be read (`process_start_token` returns ``None`` for a pid it cannot
    inspect), is left to the pid comparison alone. So this only ever *adds* a
    refusal on positive evidence, and never converts a genuine older record
    into a mismatch -- which would have reported every supervisor on the
    machine as a leftover the day it shipped.

    A `pid_start` that is *present but malformed* -- a number, a list, an
    empty string, or any value `process_identity.is_start_token` does not
    recognise -- is a mismatch rather than a fallback, for exactly the reason
    a missing `pid` is. No version of `_save_loop_code` can produce one: it
    writes either a real token or ``None``, and ``None`` is the absent case
    above. So a malformed value is damage, and the leniency here is owed to a
    real earlier schema, not to corruption. Adversarial review caught the
    first draft accepting ``17`` and ``""``.

    Two tokens of *different kinds* are not a mismatch, though, which is why
    the comparison goes through `same_start_token` rather than ``==``. The
    macOS/BSD probe changed its tag from ``ps`` to ``psc`` when it pinned its
    locale and timezone, and a record written before that describes the same
    process in another rendering. Comparing those for equality would report
    every macOS supervisor's record as not its own on the day the pin landed.

    ``boot`` closes the last gap the token leaves: `_linux_start_token` counts
    clock ticks *since boot*, so across a reboot a replacement can collide
    with its predecessor on both pid and token. `same_boot` returns ``None``
    for anything it cannot compare -- an untagged value, a record written on
    another platform, a machine whose boot source stopped answering -- and
    only ``False`` refutes.

    ``live_start`` lets a caller that has already probed this pid hand the
    answer over instead of paying for a second ``ps`` fork; :data:`_UNPROBED`
    -- the default -- means nobody has looked yet and this must. ``None`` is
    not that: it is the probe having answered "cannot tell", which is why the
    two are distinct values rather than one falsy one.
    """
    if loop_pid is None:
        return True
    recorded_pid = payload.get("pid")
    if not isinstance(recorded_pid, int) or recorded_pid != loop_pid:
        return False
    if "boot" in payload:
        recorded_boot = payload.get("boot")
        if recorded_boot is not None:
            if not isinstance(recorded_boot, str) or not recorded_boot:
                return False
            if process_identity.same_boot(
                    recorded_boot, process_identity.boot_identity()) is False:
                return False
    if "pid_start" not in payload:
        return True
    recorded_start = payload.get("pid_start")
    if recorded_start is None:
        return True
    if not process_identity.is_start_token(recorded_start):
        return False
    if live_start is _UNPROBED:
        live_start = process_identity.process_start_token(loop_pid)
    if not live_start:
        return True
    return process_identity.same_start_token(recorded_start, live_start) is not False


def _compare_recorded_files(files: object) -> "tuple[str, list[str]]":
    """Compare recorded per-file digests against what is on disk now.

    Shared by the two callers that ask the staleness question from opposite
    ends: `loop_code_state` reads another process's record off disk, and
    `own_code_state` hands over this process's own in-memory fingerprint.
    They must not drift, because they are quoted side by side -- `operator
    list` prints one and the session preamble carries the other, and two
    verdicts that disagree about the same supervisor would discredit both.
    """
    if not isinstance(files, list) or not files:
        return CODE_UNKNOWN, []

    changed: list[str] = []
    undecided = False
    for entry in files:
        if not isinstance(entry, dict):
            undecided = True
            continue
        path, recorded = entry.get("path"), entry.get("sha256")
        if not isinstance(path, str) or not path:
            undecided = True
            continue
        if not isinstance(recorded, str):
            # Nothing was known about this file when the supervisor started,
            # so nothing can be concluded about it now.
            undecided = True
            continue
        now = _digest_file(Path(path))
        if now is None:
            undecided = True
            continue
        current = "absent" if now is FILE_ABSENT else now
        if current != recorded:
            changed.append(path)

    if changed:
        return CODE_STALE, sorted(changed)
    if undecided:
        return CODE_UNKNOWN, []
    return CODE_CURRENT, []


def own_code_state() -> "tuple[str, list[str]]":
    """Has the operator source moved on since *this* process imported it?

    The same question `loop_code_state` answers about somebody else, asked
    from inside the process that is actually running the code -- which is
    strictly better evidence, and the reason this does not simply reuse the
    record on disk. Three things stop being possible:

    - The record could have failed to be written. `_save_loop_code` warns and
      carries on, by design, so a supervisor can be running with a
      `loop_code` file belonging to a *previous* supervisor of the same
      instance. Compared against disk that stale record can read
      ``current`` -- a confident all-clear sourced from a process that no
      longer exists.
    - The record could be unreadable, which costs a verdict this process
      never needed to go to disk for.
    - The record has no owner stamped into it that a reader is obliged to
      check, so nothing distinguishes those two cases from a good one.

    `running_code_fingerprint` is cached for the life of the process, so the
    left-hand side here is what was really imported, not a re-read of disk.
    That cache is what makes the comparison mean anything: recomputing both
    sides would compare disk against disk and always say ``current``.

    Never returns ``CODE_UNRECORDED``: an in-memory fingerprint always
    exists, so "nobody wrote it down" is not one of the available answers.
    """
    return _compare_recorded_files(running_code_fingerprint().get("files"))


def _launch_code_state() -> str:
    """`own_code_state` for the per-launch preamble, which may not raise.

    This runs inside an unattended supervisor's launch loop, where an
    unhandled exception ends the run and takes every future session with it.
    A staleness verdict is never worth that, so anything unexpected degrades
    to ``CODE_UNKNOWN``.

    Degrading to ``CODE_UNKNOWN`` rather than ``CODE_CURRENT`` is the whole
    point: the failure directions are not symmetric. ``CODE_UNKNOWN`` prints
    a caveat the agent may not need, which costs a few lines. ``CODE_CURRENT``
    prints a clean bill of health nobody checked, which is exactly the silent
    all-clear this instrument was built to stop -- and it would be
    indistinguishable from the healthy case, so nothing downstream could ever
    catch it.
    """
    try:
        return own_code_state()[0]
    except Exception as exc:  # pragma: no cover - defensive
        log(f"  Warning: could not check whether this supervisor is current: {exc}")
        return CODE_UNKNOWN
