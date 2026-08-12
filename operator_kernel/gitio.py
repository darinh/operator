"""Git as an observation, not an action.

These four are the only way the kernel reads a repository, and they are
grouped because they were the cycle: the fingerprint needed the worktree
list and the worktree list needed the git runner. One module, one
direction, and both callers import from here.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

from config import GIT_PROBE_TIMEOUT, IS_WINDOWS, NO_WINDOW_KWARGS
from presence import file_present

def _worktree_paths(cwd: Path) -> list[Path] | None:
    """Every checkout attached to this repository, primary first."""
    out = _git_output(["worktree", "list", "--porcelain"], cwd)
    if out is None:
        return None
    paths = [line[len("worktree "):].strip()
             for line in out.splitlines() if line.startswith("worktree ")]
    if not paths:
        # A repository always has at least its primary checkout, so an empty
        # list means the output was not what we think it was.
        return None
    return [Path(p) for p in paths]


def _uncommitted_content(path: Path) -> str | None:
    """The *content* of everything uncommitted in one worktree.

    ``git status`` names the paths that changed and how, but not what is in
    them: a file edited twice produces the identical ``" M app.py"`` line
    both times. A loop iterating on the same uncommitted file would therefore
    fingerprint identically session after session and be stopped for making
    no progress, which is the most expensive way this breaker can be wrong.

    ``git diff`` (worktree against index) and ``git diff --cached`` (index
    against HEAD) together cover every tracked byte, and neither needs HEAD to
    exist — in a repository with no commits yet ``--cached`` diffs against the
    empty tree instead of failing, so no unborn-HEAD special case is needed.
    Untracked files are in neither diff, so their contents are hashed
    separately. ``--exclude-standard`` applies the ignore rules, which is what
    keeps build output and ``__pycache__`` from churning the fingerprint on
    every run and silently disarming the breaker. Entries ending in ``/`` are
    skipped: git does not descend into a nested repository, so it reports one
    collapsed directory entry that ``hash-object`` cannot read. A linked
    worktree is the common case and loses nothing, because every worktree is
    fingerprinted in its own right; anything else still registers through the
    ``git status`` line that names it.

    ``None`` means the question could not be answered.
    """
    parts = []
    for args in (["diff"], ["diff", "--cached"]):
        out = _git_output(args, path)
        if out is None:
            return None
        parts.append(out)
    untracked = _git_output(
        ["ls-files", "--others", "--exclude-standard", "-z"], path)
    if untracked is None:
        return None
    names = [n for n in untracked.split("\0") if n and not n.endswith("/")]
    # ``ls-files -z`` is NUL-delimited precisely because a filename may
    # contain a newline, but ``hash-object --stdin-paths`` is newline-
    # delimited and has no NUL equivalent. Feeding it such a name splits it
    # into two paths that do not exist, so the call fails, the fingerprint
    # collapses to "unknown", and the breaker is off for the rest of the run
    # — silently, because "unknown" is indistinguishable from an unreadable
    # repository. The rare name goes through argv, where no delimiter exists
    # to be confused, and the common case keeps its single batched call.
    batched = [n for n in names if "\n" not in n]
    individually = [n for n in names if "\n" in n]
    if batched:
        # One batched call: the file count is unbounded, and a process per
        # file would make the probe's cost scale with someone else's mess.
        hashed = _git_output_with_input(
            ["hash-object", "--stdin-paths"], path, "\n".join(batched) + "\n")
        if hashed is None:
            return None
        parts.append(hashed)
    for name in individually:
        hashed = _git_output(["hash-object", "--", name], path)
        if hashed is None:
            return None
        # The name is digested alongside its content: two such files swapping
        # contents would otherwise produce the same unordered set of hashes.
        parts.append(f"{name}\0{hashed}")
    return "\0".join(parts)


def _git_output_with_input(args: list[str], cwd: Path,
                           stdin_text: str) -> str | None:
    """``_git_output`` for a command that reads paths on stdin.

    Same contract: every failure mode collapses to ``None``, meaning
    "unknown", never "nothing changed".
    """
    try:
        proc = subprocess.run(
            ["git", *args], cwd=str(cwd), input=stdin_text,
            capture_output=True, text=True,
            encoding="utf-8", errors="replace",
            timeout=GIT_PROBE_TIMEOUT, **NO_WINDOW_KWARGS,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout


def _git_output(args: list[str], cwd: Path) -> str | None:
    """Run a read-only git command. ``None`` when it could not be answered.

    Every failure mode collapses to ``None`` on purpose: git missing, the
    directory not being a repository, a lock held by whoever is working in
    there, a timeout. The caller must treat that as "unknown", never as
    "nothing changed".
    """
    try:
        proc = subprocess.run(
            ["git", *args], cwd=str(cwd), capture_output=True, text=True,
            encoding="utf-8", errors="replace",
            timeout=GIT_PROBE_TIMEOUT, **NO_WINDOW_KWARGS,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout
