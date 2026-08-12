"""Extracted from copilot_operator.py: shared paths, identity and helpers."""
from __future__ import annotations

import json
import os
import platform
import re
import shutil
import subprocess
import sys
import time
import hashlib
import sqlite3
import ntpath
from datetime import datetime, timezone
from pathlib import Path
from config import CATALOG_UNREADABLE, GIT_PROBE_TIMEOUT, IS_WINDOWS, METRICS_DB, NO_WINDOW_KWARGS
from presence import file_present














def show_run_summary(run_started: str) -> None:
    if not run_started or file_present(METRICS_DB) is not True:
        return
    print("\n═══ Operator Run Summary ═══\n")
    try:
        rows, headers = _query(f"""
            SELECT COUNT(*) AS sessions,
                   printf('%.1f', {_credits()}) AS credits,
                   printf('$%.2f', {_usd()}) AS est_cost,
                   COALESCE(SUM(api_time_seconds) || 's','—') AS total_api_time,
                   COALESCE({_fmt_duration_sql('SUM(session_time_seconds)')},'—') AS total_sess_time,
                   COALESCE('+' || SUM(lines_added) || ' -' || SUM(lines_removed),
                            '—') AS total_changes
            FROM sessions WHERE no_op = 0 AND ended_at >= ?
        """, (run_started,))
    except sqlite3.Error as exc:
        # This runs on every shutdown path. A summary that cannot be read is
        # not a reason to end a clean shutdown with a traceback.
        print(f"  (metrics unavailable: {exc})")
        return
    print(_table(rows, headers))
    try:
        rows, headers = _query(f"""
            SELECT m.model_name AS model,
                   printf('%.1f', COALESCE(SUM(m.nano_aiu),0) / {_NANO}.0) AS credits,
                   COUNT(*) AS uses
            FROM model_usage m JOIN sessions s ON m.session_id = s.id
            WHERE s.no_op = 0 AND s.ended_at >= ?
            GROUP BY m.model_name ORDER BY SUM(m.nano_aiu) DESC
        """, (run_started,))
    except sqlite3.Error:
        return
    if rows:
        print()
        print(_table(rows, headers))


def project_handoff_file(cwd: Path,
                         instance_id: str = "") -> "Path | None | _CatalogUnreadable":
    """Resolve the handoff path for a project directory.

    Looks the directory up in ``~/.operator/projects/catalog.csv`` (the same
    catalog ``handoff``/``handoff_tool.py`` use) and returns the path the
    handoff file *would* live at, regardless of whether it currently exists.
    Returns None if the directory has no catalog entry at all, and
    :data:`CATALOG_UNREADABLE` if the catalog could not be read, which is a
    different answer and must not share a return value with the first.

    Handoffs are keyed by **instance**: ``handoff/{instance_id}.md``. An empty
    ``instance_id`` yields the project directory's legacy ``next-session.md``,
    which is what a pre-migration project still has on disk and what a caller
    with no instance in hand can meaningfully ask about.

    The lookup is keyed on the primary checkout, so running from a worktree
    finds the project's real entry instead of reporting it unregistered.

    The presence probe is spent on ``is False`` and only ``is False``, for the
    reason :func:`handoff_tool.resolve_guid` spells out against this same file:
    a denied *stat* does not imply a denied *read*, so a catalog sitting behind
    an unsearchable parent still gets opened. Gating the read on the stat could
    only ever subtract a lookup that would have succeeded -- measured: the stat
    raises EACCES while ``open`` hands the bytes over.
    """
    catalog = project_catalog_path()
    if file_present(catalog) is False:
        return None
    # "No row matched" is only an answer if every row was actually compared.
    undecided = False
    try:
        target = str(primary_repo_root(cwd).resolve())
    except (OSError, ValueError, RuntimeError):
        # Nothing can be compared against a target that will not resolve, so
        # every row below is undecided rather than unmatched. Reporting "not
        # registered" here would tell a restarting session its project has no
        # handoff, which is the one thing this must never say on a guess.
        return CATALOG_UNREADABLE
    if IS_WINDOWS:
        target = target.lower()
    try:
        with open(catalog, "r", encoding="utf-8", errors="replace", newline="") as fh:
            for row in catalog_rows(fh):
                if row is None:
                    # The line would not parse at all. Same reasoning as an
                    # unresolvable row below: it is a row not compared, not a
                    # row that failed to match.
                    undecided = True
                    continue
                if len(row) < 2:
                    continue
                path, guid = row[0].strip().strip('"'), row[1].strip().strip('"')
                # The same predicate the writer uses, imported rather than
                # copied: two definitions of "valid project id" that drift
                # apart is the very bug this rejects. A row the writer refuses
                # to create must not be one the reader will happily open --
                # `../../elsewhere` resolved two levels outside the projects
                # root, and on Windows `victim.` is `victim`, another
                # project's handoff.
                if not path or not guid_is_usable(guid):
                    continue
                try:
                    resolved = str(Path(path).resolve())
                except (OSError, ValueError, RuntimeError):
                    # This row could not be compared. Skipping it is right, but
                    # it means the "not registered" verdict below is no longer
                    # established for this catalog. All three arrive here: the
                    # catalog is a hand-edited CSV, so a row can name a symlink
                    # loop (RuntimeError, or OSError(ELOOP) on newer
                    # interpreters) or carry an embedded NUL (ValueError) just
                    # as easily as it can name a denied path (OSError).
                    undecided = True
                    continue
                if IS_WINDOWS:
                    resolved = resolved.lower()
                if resolved == target:
                    base = project_dir(guid)
                    if instance_id:
                        return base / "handoff" / f"{instance_id}.md"
                    return base / "next-session.md"
    except OSError:
        return CATALOG_UNREADABLE
    return CATALOG_UNREADABLE if undecided else None






# ── launching ───────────────────────────────────────────────────
def project_catalog_path() -> Path:
    return projects_root() / "catalog.csv"
