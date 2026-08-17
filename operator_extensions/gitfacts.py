"""Read-only git facts, bounded in time, for extensions that must not guess.

Three constraints shape every line here, and none of them are stylistic.

**It may not mutate anything.** Not "should not" -- the mutating verbs (`stash`,
`reset`, `clean`, `checkout`, `restore`, `rm`, `mv`, `commit`, `merge`, `push`,
`worktree add`, `worktree remove`, `worktree prune`, `branch -d`) do not appear
in this module, and `tests/test_extension_packaging.py` asserts their absence
over the parsed source rather than only over the paths its own cases reach. An
extension proposes; the kernel disposes. A janitor that deleted the worktree it
was about to propose removing would be the whole design inverted, and the
directory it deleted may hold a human's uncommitted afternoon.

**It may not hang.** Every call has a timeout, and the caller gets a budget it
can spend across several repositories, because `extensions.Host` kills a worker
at `DEFAULT_DEADLINE` and a killed worker is a `Deadline` failure that
quarantines the extension for the life of the host. An extension that scans one
repository too many stops answering for the rest of the run.

**It may not raise.** A missing `git`, a directory that is not a repository, a
network filesystem that has gone away -- each is an answer of "I could not
tell", which every hook here turns into no opinion. A traceback out of a hook is
a `Failure`, and a `Failure` on `admit_launch` is an extension that is blind
rather than one that is quiet.

`cwd` is never inherited. `extensions.Host` gives each worker a private
temporary directory precisely so a hook cannot write into the supervised
repository by using a relative path, so every command here names its repository
with `-C` and nothing here depends on where the process happens to be standing.
"""
from __future__ import annotations

import dataclasses
import subprocess
import time
from pathlib import Path

#: Seconds for one git invocation. Short: these are local, metadata-only
#: queries, and one that takes longer than this is a repository on a filesystem
#: that has stopped answering -- which is a thing to give up on rather than to
#: wait out on the launch path.
COMMAND_TIMEOUT = 5.0

#: Seconds a hook may spend on git in total, across every repository it looks
#: at. Comfortably inside `extensions.DEFAULT_DEADLINE` (10s), because the
#: margin has to cover interpreter start, imports and the reply -- and because
#: overrunning it is not a slow answer but a quarantine.
SCAN_BUDGET = 6.0

#: The half-finished states a session must not be started into, mapped to the
#: marker git leaves in the git directory. A directory for the two rebase
#: flavours, a file for the rest; `Path.exists()` answers both.
IN_PROGRESS = (
    ("merge", "MERGE_HEAD"),
    ("rebase", "rebase-merge"),
    ("rebase", "rebase-apply"),
    ("cherry-pick", "CHERRY_PICK_HEAD"),
    ("revert", "REVERT_HEAD"),
    ("bisect", "BISECT_LOG"),
)


class Budget:
    """A wall-clock allowance shared across several git calls.

    A plain deadline rather than a token bucket: the question is only ever "is
    there time for one more repository?", and a caller that asks after the
    answer is no gets a bounded refusal instead of an unbounded scan.
    """

    def __init__(self, seconds: float = SCAN_BUDGET, clock=time.monotonic):
        self.clock = clock
        self.expires = clock() + seconds

    def remaining(self) -> float:
        return max(0.0, self.expires - self.clock())

    def spent(self) -> bool:
        return self.remaining() <= 0.0


@dataclasses.dataclass(frozen=True)
class Worktree:
    """One line group from `git worktree list --porcelain`.

    `branch` is the short name (`main`), not the ref (`refs/heads/main`), and
    is empty for a detached head -- which is why `detached` is a separate field
    rather than being inferred from an empty branch: "detached" and "git did not
    tell us" are different, and a janitor that confused them would propose
    removing a checkout on the strength of a parse failure.
    """

    path: str
    head: str = ""
    branch: str = ""
    detached: bool = False
    bare: bool = False
    locked: bool = False
    prunable: bool = False


def _run(root, args: "list[str]", timeout: float) -> "tuple[bool, str]":
    """One read-only git command against `root`. Never raises.

    Returns `(ok, stdout)`. `ok` is false for a non-zero exit, a timeout, a
    missing executable and an unreadable directory alike, because no hook here
    distinguishes them: each one means the answer is unknown, and unknown means
    no opinion.
    """
    if timeout <= 0.0:
        return False, ""
    try:
        done = subprocess.run(
            ["git", "-C", str(root), *args],
            capture_output=True, text=True, timeout=timeout,
            # Never a shell. The arguments include branch names, which are
            # attacker-influenced in any repository that takes a pull request.
            shell=False,
        )
    except (OSError, ValueError, subprocess.SubprocessError):
        return False, ""
    if done.returncode != 0:
        return False, ""
    return True, done.stdout


def is_repo(root, budget: "Budget | None" = None) -> bool:
    ok, out = _run(root, ["rev-parse", "--is-inside-work-tree"],
                   _timeout(budget))
    return ok and out.strip() == "true"


def git_dir(root, budget: "Budget | None" = None) -> "Path | None":
    """This worktree's own git directory, absolute.

    `--absolute-git-dir` rather than `--git-dir`, which answers relative to the
    process's directory -- and this process is standing in a private temporary
    directory that has nothing to do with the repository.

    Note that for a linked worktree this is the *per-worktree* directory
    (`.../.git/worktrees/name`), which is the right one for the in-progress
    markers: a rebase in one worktree must not be reported against another.
    """
    ok, out = _run(root, ["rev-parse", "--absolute-git-dir"], _timeout(budget))
    if not ok or not out.strip():
        return None
    return Path(out.strip())


def unfinished(root, budget: "Budget | None" = None) -> str:
    """The half-finished git operation in `root`, or an empty string.

    Empty for "none" *and* for "could not tell", and the two are deliberately
    collapsed here because every caller does the same thing with them: an
    extension that cannot read the repository has no grounds to refuse a launch
    into it.
    """
    where = git_dir(root, budget)
    if where is None:
        return ""
    for name, marker in IN_PROGRESS:
        try:
            if (where / marker).exists():
                return name
        except OSError:
            continue
    return ""


def worktrees(root, budget: "Budget | None" = None) -> "list[Worktree]":
    ok, out = _run(root, ["worktree", "list", "--porcelain"], _timeout(budget))
    return parse_worktrees(out) if ok else []


def parse_worktrees(text: str) -> "list[Worktree]":
    """Parse `git worktree list --porcelain`.

    Separated from the command that produces it so the parser can be tested
    against output this machine's git version does not emit -- `prunable` and
    `locked` reasons arrived in 2.36, and a parser that only ever sees the
    local version's output is pinned to an accident of the developer's laptop.

    Groups are separated by a blank line and begin with `worktree <path>`. A
    stray group with no `worktree` line is dropped rather than merged into its
    neighbour: attaching one checkout's attributes to another is how a janitor
    proposes deleting the wrong directory.
    """
    found: list[Worktree] = []
    current: dict = {}

    def flush() -> None:
        if current.get("path"):
            found.append(Worktree(**current))
        current.clear()

    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            flush()
            continue
        key, _, value = line.partition(" ")
        if key == "worktree":
            flush()
            current["path"] = value.strip()
        elif not current.get("path"):
            continue
        elif key == "HEAD":
            current["head"] = value.strip()
        elif key == "branch":
            current["branch"] = value.strip().removeprefix("refs/heads/")
        elif key == "detached":
            current["detached"] = True
        elif key == "bare":
            current["bare"] = True
        elif key == "locked":
            current["locked"] = True
        elif key == "prunable":
            current["prunable"] = True
    flush()
    return found


def is_merged(root, branch: str, into: str,
              budget: "Budget | None" = None) -> bool:
    """Does `into` already contain every commit on `branch`?

    `--contains` against the branch tip, rather than `git branch --merged`,
    because the latter answers relative to whatever HEAD happens to be in the
    worktree this runs against.

    False when the question could not be asked -- an unknown integration
    branch, a repository that has gone away -- because this answer is the sole
    grounds on which the janitor proposes removing a checkout, and "could not
    tell" must never read as "safe to delete".
    """
    if not branch or not into:
        return False
    ok, out = _run(root, ["branch", "--format=%(refname:short)",
                          "--contains", branch, "--list", into],
                   _timeout(budget))
    return ok and into in out.split()


def has_changes(root, budget: "Budget | None" = None) -> "bool | None":
    """Whether the working tree has uncommitted changes, or None if unknown.

    Tri-state on purpose, and it is the one function here that does not
    collapse "no" into "could not tell". The janitor uses this as a *veto* on
    proposing a removal, so an unreadable repository has to be able to say
    "stop" rather than "nothing to see".
    """
    ok, out = _run(root, ["status", "--porcelain"], _timeout(budget))
    if not ok:
        return None
    return bool(out.strip())


def _timeout(budget: "Budget | None") -> float:
    if budget is None:
        return COMMAND_TIMEOUT
    return min(COMMAND_TIMEOUT, budget.remaining())
