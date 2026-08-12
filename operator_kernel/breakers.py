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
from gitio import _git_output, _uncommitted_content, _worktree_paths

from config import GIT_PROBE_TIMEOUT, NO_WINDOW_KWARGS
from presence import dir_present, status



def workspace_fingerprint(cwd: Path) -> str | None:
    """Digest of everything in this repository a session could have changed.

    ``None`` means the question could not be answered, which is deliberately
    distinct from "nothing changed".

    Scope is the whole repository, not ``cwd``. Work here happens on branches
    in linked worktrees under ``.worktrees/``, so a session can commit an
    entire feature without the primary checkout's HEAD or ``git status``
    moving at all. Fingerprinting only the current directory would therefore
    report a productive session as idle and eventually stop a loop that was
    working perfectly. Every local ref and every worktree's uncommitted state
    is included instead.

    ``refs/remotes`` is included for the same reason: a session that commits,
    pushes, then deletes its local branch and worktree leaves local state
    exactly as it found it, and only the remote-tracking ref still records
    that the work happened.

    A detached HEAD is covered by each worktree's ``# branch.oid`` line
    rather than by the refs, because a commit made while detached advances no
    ref at all.
    """
    refs = _git_output(
        ["for-each-ref", "--format=%(objectname) %(refname)",
         "refs/heads", "refs/tags", "refs/stash", "refs/remotes"], cwd)
    if refs is None:
        return None
    worktrees = _worktree_paths(cwd)
    if worktrees is None:
        return None

    digest = hashlib.sha256()
    digest.update(refs.encode("utf-8", "replace"))
    for path in worktrees:
        present = dir_present(path)
        if present is False:
            # git still lists a worktree whose directory has been removed. It
            # cannot be holding changes, so it is recorded as absent rather
            # than making the whole fingerprint unknown.
            digest.update(f"\0{path}\0<absent>".encode("utf-8", "replace"))
            continue
        if present is None:
            return None
        # ``--branch`` in the v2 format is what makes a detached HEAD
        # visible. A commit on a detached checkout advances no ref, so
        # ``for-each-ref`` above cannot see it and the tree is clean
        # afterwards — the whole repository would fingerprint identically
        # across a session that committed real work, and the breaker would
        # stop a loop that was being productive. ``# branch.oid`` carries the
        # commit itself. v2 is used rather than a second ``rev-parse`` probe
        # because it costs no extra process and, unlike ``rev-parse HEAD``,
        # it still exits 0 in a repository with no commits yet (reporting
        # ``(initial)``) rather than failing and reading as "unknown".
        status = _git_output(
            ["status", "--porcelain=v2", "--branch",
             "--untracked-files=all"], path)
        if status is None:
            # The worktree we cannot read is exactly the one that might hold
            # the change, so no verdict is available for the repository.
            return None
        content = _uncommitted_content(path)
        if content is None:
            return None
        digest.update(f"\0{path}\0".encode("utf-8", "replace"))
        digest.update(status.encode("utf-8", "replace"))
        digest.update(content.encode("utf-8", "replace"))
    return digest.hexdigest()


def evaluate_progress(count: int | None, before: str | None,
                      after: str | None, *,
                      ending_accounted_for: bool) -> tuple[int | None, str]:
    """Fold one finished session into the no-change counter.

    Returns ``(count, verdict)`` where verdict is ``changed``, ``unchanged``,
    ``unaccounted`` or ``unknown``. An unknown session leaves the counter
    exactly as it was: it neither counts toward stopping the loop nor clears
    what came before.

    Progress is judged before the counter is consulted, so a session that
    demonstrably changed something resets the streak to zero even when the
    previous count was unreadable. Reporting known progress as "unknown"
    would leave a corrupt counter file corrupt forever, and a breaker that
    can never be re-armed is one that has silently switched itself off.

    An unreadable count is healed the same way when the session demonstrably
    changed *nothing*, by restarting the streak at one. The argument is the
    same one, and leaving it out was a real hole: a stuck agent is exactly
    the case where no session ever writes the counter, so a file that went
    corrupt would stay corrupt and the breaker would be off for the rest of
    the run — precisely when it was needed. Restarting at one can only ever
    undercount (the streak really is at least this session), so the error it
    can make is letting the loop run longer, never stopping a healthy one.

    ``ending_accounted_for`` is what keeps this counter about *idleness*.
    Changing nothing is evidence that an agent had nothing to do only if the
    session ended the way the loop expects — a handoff, or an exit the runner
    saw. A session killed from outside has usually not committed at the point
    it dies, so it is indistinguishable from an idle one by fingerprint
    alone, and charging it here retires the loops being killed fastest. Such
    a session is not evidence *either way*: it neither advances this streak
    nor clears it, exactly like an unmeasurable one. It is counted separately
    by ``evaluate_unaccounted``, so the loop stays bounded.

    There is deliberately no default. The caller must decide, because the
    silent version of this parameter is the bug it exists to fix.
    """
    if before is None or after is None:
        return count, "unknown"
    if before != after:
        return 0, "changed"
    if not ending_accounted_for:
        # Not healed to 1 here as an unreadable count would be for a genuine
        # no-change session: there is nothing to heal *from*. This session
        # says nothing about idleness, so inventing a streak length from it
        # would be entering a guess as an observation.
        return count, "unaccounted"
    if count is None:
        return 1, "unchanged"
    return count + 1, "unchanged"


def evaluate_unaccounted(count: int | None, verdict: str) -> int | None:
    """Fold the same session into the unaccounted-ending streak.

    Takes ``evaluate_progress``'s verdict rather than re-deciding, so the two
    counters cannot disagree about what the session was.

    A session that changed something clears this streak as well: whatever
    ended it, work landed, and the loop is worth continuing. Anything the
    fingerprint could not settle leaves it alone, for the same reason the
    other counter is left alone — "could not tell" is not "ended badly".
    """
    if verdict == "changed":
        return 0
    if verdict != "unaccounted":
        return count
    if count is None:
        return 1
    return count + 1
