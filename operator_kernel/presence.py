"""Presence as a three-valued question.

Extracted from the install manifest, which had 26 public definitions and
lent the kernel five. The distinction these carry is the most repeated
lesson in the system they came from: a path that is absent and a path
that could not be examined are different answers, and only positive
evidence may authorise deletion, takeover or a confident report.
"""
from __future__ import annotations
import hashlib
import json
import os
import re
import stat
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable
from copilot_tools_version import __version__ as CURRENT_VERSION


#: Digest algorithm. See the module docstring for why this one.
ALGORITHM = "sha256"


_CHUNK = 1024 * 1024


# ── artifact states ──────────────────────────────────────────────
#: Nothing is deployed at the destination.
ABSENT = "absent"


#: Deployed content matches the repository. Nothing to do.
CURRENT = "current"


#: Deployed content differs from the repository but matches what setup wrote,
#: so the user never touched it and it can be replaced without asking.
STALE = "stale"


#: Deployed content differs from what setup wrote. The user edited it.
MODIFIED = "modified"


#: Something is deployed but the manifest has no record of writing it, so its
#: provenance is unknown and it is treated as precious.
UNTRACKED = "untracked"


#: Something is at the destination but it could not be examined at all, so
#: nothing is known about it — not even whether it is a file. Distinct from
#: ``ABSENT`` because absent is the one answer that licenses writing.
UNREADABLE = "unreadable"


# ── presence ─────────────────────────────────────────────────────
def path_present(path: Path) -> bool | None:
    """Whether anything is at ``path``: True, False, or None for "cannot tell".

    ``Path.exists`` is the obvious way to ask and gets it wrong in both
    directions on the interpreters this project supports.

    It *raises* on a permission denial — verified on 3.11 and on 3.12, so on
    the whole CI matrix — because EACCES is not in pathlib's ignore list. One
    artifact whose parent directory the user cannot traverse therefore aborts
    the entire setup run, and every other artifact goes uninstalled for a
    reason the traceback does not name.

    It *returns False* for the codes those two lists do hold: WINERROR 21 (in
    ``_IGNORED_WINERRORS``), a drive that exists but is not ready, which is
    what a disconnected network home looks like; and ELOOP and EBADF (in
    ``_IGNORED_ERRNOS``). Each reports a path that is not absent as
    absent, and absent is the one state that lets an installer write without
    asking.

    So only ``FileNotFoundError`` means gone. ``NotADirectoryError`` means gone
    too: a component of the path is a file, so nothing can exist below it.
    Every other ``OSError`` means we could not look, which is a different
    answer and must not share a return value with the first two.

    ``lstat`` rather than ``stat``, so a symlink whose target has been deleted
    reads as present. ``Path.exists`` follows it, finds nothing and says False;
    the installer then writes over "nothing", the write follows the link, and
    the repository's copy lands wherever the link pointed instead of here.
    """
    try:
        os.lstat(path)
    except (FileNotFoundError, NotADirectoryError):
        return False
    except OSError:
        return None
    except ValueError:
        # A path the OS cannot represent at all (embedded NUL, and on Windows
        # some reserved names). Nothing can be there, and nothing can be
        # written there either.
        return False
    return True


def dir_present(path: Path) -> bool | None:
    """Whether ``path`` is a directory: True, False, or None for "cannot tell".

    Follows symlinks, since a link to a directory is usable as one. That is
    also the limit of what False means here. An exception type is a claim
    about what the *call* did, not about what the object *is*: ``stat`` raises
    ``FileNotFoundError`` for a dangling symlink, whose own directory entry is
    very much there. So False reads as "not usable as a directory", never as
    "the path is free". Callers for whom those differ must ask
    ``path_present`` -- which does not follow the link -- as well.
    """
    try:
        return stat.S_ISDIR(os.stat(path).st_mode)
    except (FileNotFoundError, NotADirectoryError):
        return False
    except (OSError, ValueError):
        return None


def file_present(path: Path) -> bool | None:
    """Whether ``path`` is a regular file: True, False, or None.

    The same caveat as ``dir_present``: False means "not usable as a regular
    file", which a directory, a fifo and a dangling symlink all are.
    """
    try:
        return stat.S_ISREG(os.stat(path).st_mode)
    except (FileNotFoundError, NotADirectoryError):
        return False
    except (OSError, ValueError):
        return None


# ── hashing ──────────────────────────────────────────────────────
def file_digest(path: Path) -> str | None:
    """Digest of one file, or None when it cannot be read.

    ``hashlib.file_digest`` (3.11+) reads straight into the hash object's
    buffer; the fallback below is the same thing done by hand for 3.10.
    """
    try:
        with open(path, "rb") as handle:
            if hasattr(hashlib, "file_digest"):
                return hashlib.file_digest(handle, ALGORITHM).hexdigest()
            digest = hashlib.new(ALGORITHM)
            for chunk in iter(lambda: handle.read(_CHUNK), b""):
                digest.update(chunk)
            return digest.hexdigest()
    except OSError:
        return None


def tree_digest(root: Path) -> str | None:
    """Digest of a directory tree, or None when it cannot be read.

    Relative paths are folded into the hash alongside contents so that renaming
    a file changes the digest, and they are sorted so the result does not depend
    on directory iteration order — which differs between filesystems and would
    otherwise make a tree look modified after merely being copied.
    """
    try:
        if not root.is_dir():
            return None
    except OSError:
        return None
    digest = hashlib.new(ALGORITHM)
    try:
        files = sorted(
            (p for p in root.rglob("*") if p.is_file()),
            key=lambda p: p.relative_to(root).as_posix(),
        )
        for path in files:
            digest.update(path.relative_to(root).as_posix().encode("utf-8"))
            digest.update(b"\0")
            with open(path, "rb") as handle:
                for chunk in iter(lambda: handle.read(_CHUNK), b""):
                    digest.update(chunk)
            digest.update(b"\0")
    except OSError:
        return None
    return digest.hexdigest()


def digest_for(path: Path) -> str | None:
    """Digest of a file or a directory, whichever ``path`` names.

    Both probes are guarded. ``Path.is_dir`` and ``Path.exists`` raise on a
    permission denial rather than answering it, and a destination that is a
    link into a directory the user cannot traverse reaches here having already
    passed :func:`path_present` — ``lstat`` succeeded on the link itself while
    ``stat`` through it does not. Being unable to take a digest is not a reason
    to abort a whole setup run: None already means "cannot prove setup wrote
    this", which every caller treats as a reason to ask rather than to write.
    """
    try:
        if path.is_dir():
            return tree_digest(path)
    except OSError:
        return None
    return file_digest(path)


def entry(manifest: dict, key: str) -> dict | None:
    value = manifest.get("artifacts", {}).get(key)
    return value if isinstance(value, dict) else None


def classify(manifest: dict, key: str, dest: Path, source_digest: str | None) -> str:
    """Decide how ``dest`` relates to the repository and to what setup wrote.

    ``source_digest`` is the digest of the repository copy. The deployed file is
    hashed here so callers cannot pass a stale value.

    A destination that cannot be examined is ``UNREADABLE``, never ``ABSENT``.
    See :func:`path_present` for why those are different questions.
    """
    present = path_present(dest)
    if present is None:
        return UNREADABLE
    if not present:
        return ABSENT
    known = entry(manifest, key)
    if known and known.get("linked"):
        # A junction or symlink points at the repository, so it is current by
        # construction and has no independent content to compare.
        return CURRENT
    deployed_digest = digest_for(dest)
    if deployed_digest is None:
        return UNTRACKED
    if source_digest is not None and deployed_digest == source_digest:
        return CURRENT
    if known is None:
        return UNTRACKED
    return STALE if deployed_digest == known.get(ALGORITHM) else MODIFIED


# ── reporting ────────────────────────────────────────────────────
@dataclass
class ArtifactStatus:
    key: str
    kind: str
    dest: Path
    state: str
    installed_version: str | None


def status(
    manifest: dict,
    artifacts: Iterable[tuple[str, str, Path, Path]],
) -> list[ArtifactStatus]:
    """Classify each ``(key, kind, source, dest)`` against the manifest."""
    report = []
    for key, kind, source, dest in artifacts:
        known = entry(manifest, key)
        report.append(ArtifactStatus(
            key=key,
            kind=kind,
            dest=dest,
            state=classify(manifest, key, dest, digest_for(source)),
            installed_version=(known or {}).get("version"),
        ))
    return report
