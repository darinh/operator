"""Extracted from copilot_operator.py: shared paths, identity and helpers."""
from __future__ import annotations

import json
import csv
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
from mux import _POPEN_KWARGS
from config import operator_home
from config import CATALOG_UNREADABLE, GIT_PROBE_TIMEOUT, IS_WINDOWS, NO_WINDOW_KWARGS
from presence import file_present















class CatalogLookup:
    """What the project catalog said about a directory.

    Three answers, kept apart on purpose: a guid, "no row matched", and "the
    question could not be settled". The third is why this is a type rather than
    an `Optional[str]` -- `project_handoff_file` already learned that collapsing
    "not registered" into "could not read" tells a restarting session its
    project has no handoff on the strength of a guess.
    """

    __slots__ = ("guid", "undecided")

    def __init__(self, guid: "str | None" = None, undecided: bool = False):
        self.guid = guid
        self.undecided = undecided

    def __repr__(self) -> str:
        return f"CatalogLookup(guid={self.guid!r}, undecided={self.undecided})"

    def __eq__(self, other) -> bool:
        return (isinstance(other, CatalogLookup)
                and other.guid == self.guid
                and other.undecided == self.undecided)


def catalog_guid(cwd: Path) -> CatalogLookup:
    """The project guid registered for ``cwd``, if the catalog settles it.

    `supervisor.py` called this by name for the whole life of the extraction
    and **nothing defined it**. The call sits behind `if store is None: return`,
    and no entry point injects a store yet, so the `NameError` was unreachable
    in practice and became reachable the moment work assignment was switched
    on -- where `_loop_work_db`'s `except Exception` would have caught it and
    answered `None`, which reaches the agent as "you have no assignment".

    That is the second time this exact subsystem has been broken by a bare name
    nothing imported, and the first time is written up in
    `tests/test_work_assignment.py`. The lookup itself was never missing: it was
    fused inside :func:`project_handoff_file`, which needed the same three
    answers and had already worked out how to keep them apart. It is lifted out
    here rather than reimplemented, because two readings of one hand-edited CSV
    is how the reader starts accepting rows the writer would refuse.

    The presence probe is spent on ``is False`` and only ``is False``: a denied
    *stat* does not imply a denied *read*, so a catalog behind an unsearchable
    parent still gets opened.
    """
    catalog = project_catalog_path()
    if file_present(catalog) is False:
        return CatalogLookup(None)
    # "No row matched" is only an answer if every row was actually compared.
    undecided = False
    try:
        target = str(primary_repo_root(cwd).resolve())
    except (OSError, ValueError, RuntimeError):
        # Nothing can be compared against a target that will not resolve, so
        # every row below is undecided rather than unmatched.
        return CatalogLookup(None, undecided=True)
    if IS_WINDOWS:
        target = target.lower()
    try:
        with open(catalog, "r", encoding="utf-8", errors="replace",
                  newline="") as fh:
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
                    return CatalogLookup(guid)
    except OSError:
        return CatalogLookup(None, undecided=True)
    return CatalogLookup(None, undecided=undecided)


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
    finds the project's real entry instead of reporting it unregistered. It is
    :func:`catalog_guid`'s, shared with the work-assignment seam rather than
    written twice.
    """
    if file_present(project_catalog_path()) is False:
        return None
    found = catalog_guid(cwd)
    if found.undecided:
        return CATALOG_UNREADABLE
    if found.guid is None:
        return None
    base = project_dir(found.guid)
    if instance_id:
        return base / "handoff" / f"{instance_id}.md"
    return base / "next-session.md"






# ── launching ───────────────────────────────────────────────────
def project_catalog_path() -> Path:
    return projects_root() / "catalog.csv"


def projects_root() -> Path:
    """The directory holding one subdirectory per catalogued project.

    Under ``~/.operator``, not ``~/.copilot``. ``~/.copilot`` is the Copilot
    CLI's own configuration directory -- its extensions, skills, settings,
    session store and logs are all in there -- so this toolkit keeping the
    project catalog in it was squatting in another program's directory. Every
    other piece of operator state had already moved out; the catalog and the
    per-project directories had not, and they are the ones that matter most,
    because the catalog is what maps a project to its id. Lose it and you have
    not lost a preference, you have lost every project's identity and with it
    every handoff and ``superseded/`` file keyed to that id.

    Resolved on each call rather than captured at import: the tests, and anyone
    who relocates a home directory, patch ``Path.home`` and expect the writer
    and the reader to follow it to the same place.
    """
    return operator_home() / "projects"


def project_dir(guid: str) -> Path:
    """Where one project's handoff, its ``superseded/`` archive and its
    instructions live.

    Here for the same reason :func:`guid_is_usable` is: ``handoff_tool`` writes
    this path and ``copilot_operator`` reads it, and a path spelled out
    separately in the writer and the reader is a path that can drift. The
    deployed instructions quote it too, which makes a third copy -- pinned by
    ``tests/test_instructions_template.py`` against this function rather than
    against a retyped literal.
    """
    return projects_root() / guid


def primary_repo_root(start=None) -> Path:
    """The primary checkout of whatever repository ``start`` belongs to.

    ``git rev-parse --show-toplevel`` is unusable here: inside a linked
    worktree it returns the worktree. ``--git-common-dir`` is unusable too --
    it is relative (``.git``) in the primary checkout but absolute in a
    worktree, so the obvious ``dirname`` of it is wrong in one of the two
    cases. The first record of ``git worktree list --porcelain`` is always the
    primary checkout, and it reads the same from anywhere in the repository.

    Returns ``start`` unchanged when git is missing, the call fails, or the
    path is not inside a repository, so callers outside a repo keep their
    previous behaviour.

    A path that cannot be *examined* is not one of those cases. ``is_dir()``
    raises on EACCES rather than answering, and treating that as "not a
    repository" hands a worktree path back as though it were a project root --
    which is exactly the duplicate-identity failure this module exists to
    prevent. So the probe is guarded and an unexaminable path still gets the
    git call, which either answers or fails on its own terms. See
    :func:`install_manifest.path_present` for the full polarity argument.

    The encoding is named rather than inherited. ``text=True`` alone decodes
    with the locale's preferred encoding -- cp1252 on Windows -- and git emits
    paths as UTF-8 bytes, so a repository whose path contains a character
    whose UTF-8 encoding includes an undefined cp1252 byte (measured: 0x81,
    from U+0401) killed subprocess's reader thread with UnicodeDecodeError.
    The process still exited 0, so the ``returncode`` guard below let it
    through, and ``proc.stdout`` was None: the failure arrived as an
    AttributeError from the loop, i.e. "the agent does not know what project
    it is in" spelled as a crash. ``errors="replace"`` cannot raise, and the
    explicit None check keeps a read that failed from reading as a repository
    with no worktrees.
    """
    base = Path(start) if start is not None else Path.cwd()
    try:
        if not base.is_dir():
            return base
    except OSError:
        pass
    try:
        proc = subprocess.run(
            ["git", "worktree", "list", "--porcelain"],
            cwd=str(base), capture_output=True,
            encoding="utf-8", errors="replace", timeout=10,
            **_POPEN_KWARGS,
        )
    except (OSError, ValueError, subprocess.SubprocessError):
        return base
    if proc.returncode != 0 or proc.stdout is None:
        return base
    for line in proc.stdout.splitlines():
        if line.startswith("worktree "):
            candidate = line[len("worktree "):].strip()
            return Path(candidate) if candidate else base
    return base


def catalog_rows(fh):
    """Yield one parsed row per line, or ``None`` for a line that will not parse.

    This lives here for the same reason :func:`guid_is_usable` does: both the
    writer (``handoff_tool``) and the reader (``copilot_operator``) read this
    file, and two definitions of "what the catalog says" that drift apart is
    the defect, not the inconvenience.

    ``csv.reader`` given the file object aborts the *whole* iteration on the
    first line it refuses, and before Python 3.11 an embedded NUL is exactly
    such a line -- ``_csv.Error: line contains NUL``. Two things follow, and
    both are wrong for a hand-edited file. The error escapes a caller whose
    entire job is to answer "registered or not", and every row *after* the bad
    one is never compared, so one mistyped character silently unregisters
    every project below it.

    Parsing each line on its own keeps the damage the size of the mistake: a
    line that will not parse costs that line and nothing else. ``None`` says
    "this row could not be read", which is not the same as a row that read
    cleanly and did not match -- the caller counts it as undecided rather than
    as evidence of absence.

    The cost is that a quoted field containing a newline is no longer joined
    across lines. The catalog is one entry per line by construction -- the
    format the instructions template documents, and nothing in this repository
    rewrites the file -- and a project path containing a newline cannot
    round-trip through it whichever reader is used, so nothing that works
    today stops working.
    """
    for line in fh:
        try:
            yield next(csv.reader([line]), None)
        except csv.Error:
            yield None


_WINDOWS_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{n}" for n in range(1, 10)),
    *(f"LPT{n}" for n in range(1, 10)),
}


# `<>:"|?*` and the control characters cannot appear in a Windows filename.
# Letting one through does not create a directory, it raises deep inside
# `mkdir` -- and an embedded NUL raises ValueError, which is not an OSError and
# so slips straight through the usual guards.
_UNSAFE_GUID_CHARS = frozenset('<>:"|?*') | frozenset(chr(c) for c in range(32))


def guid_is_usable(guid: str) -> bool:
    """True when `guid` names exactly one directory under the projects root.

    This lives here, beside the other project-identity logic, because both the
    writer (``handoff_tool``) and the reader (``copilot_operator``) must agree
    on it. Two definitions of a valid project id that drift apart is precisely
    the defect it exists to prevent, so there is one definition and both
    import it.

    A catalog row is hand-edited often enough that its second column cannot be
    trusted to hold a GUID. A blank one is the dangerous case: ``projects /
    ""`` collapses back to the projects root itself, so the handoff lands in a
    single shared ``next-session.md`` that every project overwrites in turn --
    and the next session reads it, deletes it, and never learns it belonged to
    someone else. A separator or a `..` escapes the projects root the same way,
    just further.

    The trailing-dot rule is the subtle one, and it is the same bug wearing a
    disguise: Windows strips trailing dots and spaces from a path component, so
    ``projects/victim.`` and ``projects/victim`` are one directory. Accepting
    ``victim.`` would let a malformed row silently address a *different*
    project's handoff -- exactly the clobbering this function exists to stop.

    One collision is deliberately *not* rejected: ``abc`` and ``ABC`` are one
    directory on a case-insensitive filesystem. That is a different kind of
    fault. ``victim.`` is malformed in isolation -- it does not name what it
    appears to name -- whereas ``ABC`` names exactly ``ABC``, and the problem
    only exists if some *other* row also claims ``abc``. Catching it means
    comparing rows against each other, which belongs in a catalog check rather
    than in a predicate over one value, and rejecting case variants outright
    would break catalogs that are correct today.

    A symlink planted inside the projects root is likewise out of scope. This
    is a predicate over a string; it cannot see the filesystem, and a name that
    happens to be a link escapes the root no matter how well-formed it looks.
    Catching that needs a resolve-time containment check instead. It is not
    done here because the precondition already costs more than the exploit
    yields: anyone who can write a symlink into the projects root can just as
    easily drop a hostile ``next-session.md`` into a legitimate project's
    directory, and a handoff is read as instructions. Rejecting a resolved path
    that leaves the root would also break the user who deliberately symlinks a
    project's state onto another drive, which is a real setup and a correct
    one.
    """
    if not guid or guid != guid.strip():
        return False
    # Rejects ".", ".." and any run of dots, plus anything Windows would trim
    # down to a different name.
    if guid.strip(".") == "" or guid != guid.rstrip("."):
        return False
    if "/" in guid or "\\" in guid:
        return False
    if _UNSAFE_GUID_CHARS & frozenset(guid):
        return False
    if guid.split(".")[0].upper() in _WINDOWS_RESERVED:
        return False
    # Catches the platform-specific leftovers, notably a Windows drive-relative
    # token like `C:x`, whose final component is not the whole string.
    return guid == Path(guid).name
