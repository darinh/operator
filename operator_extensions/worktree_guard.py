"""Refuse to start a session into a half-finished git operation.

A seat whose checkout is in the middle of a merge, a rebase, a cherry-pick or a
revert is not a seat that is ready for work. `git status` in that state reports
conflicts the agent did not create, `HEAD` may be detached mid-replay, and an
agent that "fixes" the conflicts is resolving a human's merge on its behalf --
with no way for anyone to tell afterwards which side of the resolution was
whose. Backlog 0003's shape, one directory over.

This is the additive half of §5 and nothing more. The kernel owns the rule about
launching into uncommitted human work, and it must, because a citizenship
invariant that protects unsaved work cannot depend on a package being installed.
What an extension may do is *extend* it, and an unfinished merge is a state the
kernel does not check for and a human always has to resolve.

**Refusing is not free, and the failure mode is written down rather than
discovered.** A refusal is re-asked, not remembered: `held_pause` backs off to
sixty seconds and the seat asks again forever. That is the intended behaviour
for this condition -- a half-finished merge does not resolve itself and the seat
should not be burning sessions on it -- but it means a seat can sit refused
indefinitely, which from the outside looks exactly like §3.3's disguise: a
healthy fleet reading as stuck. Two things keep it honest. The refusal is in the
ledger, attributed, every time the state changes. And `worktree_janitor`
proposes the same condition into the queue a human drains, so the answer to "why
has this seat not launched since Tuesday" is somewhere a person actually looks.

**Everything unknown admits.** A workdir that is not a repository, a `git` that
is not installed, a directory that has gone away, a config nobody wrote -- each
returns no opinion at all. Fail-open is the design's requirement (§D-3) and here
it is also plain correctness: this extension knows one narrow thing, and a
launch it cannot form an opinion about is not its business.
"""
from __future__ import annotations

from pathlib import Path

from . import activation, gitfacts

#: The name a human writes in `~/.operator/extensions.json`, and the name that
#: appears in the ledger and in the operator log beside a refusal. It is also
#: the entry-point name in `pyproject.toml`; `extensions.discover` refuses a
#: name that reads as a grant of authority, and this one is a plain noun.
NAME = "worktree-guard"

#: What to say for each state git can be halfway through. The reason reaches
#: the ledger and never a session's prose (INV-AUTH), so it is written for a
#: human reading `trace.jsonl` rather than for an agent.
REASONS = {
    "merge": "an unfinished merge",
    "rebase": "an unfinished rebase",
    "cherry-pick": "an unfinished cherry-pick",
    "revert": "an unfinished revert",
    "bisect": "a bisect in progress",
}


def admit_launch(**facts):
    """May this seat start a session now?

    Returns a refusal mapping, or `None` for no opinion. `None` rather than
    `{"admit": True}`: an extension that returns an admission is asserting that
    the launch is fine, which this one has no basis to say -- it checked one
    condition. `launch_admission` treats both identically, and saying the
    weaker thing keeps the ledger honest about what was actually established.
    """
    config = activation.settings(NAME)
    if config is None:
        return None
    workdir = facts.get("workdir")
    if not isinstance(workdir, str) or not workdir.strip():
        return None
    try:
        root = Path(workdir)
    except (OSError, ValueError):
        return None

    budget = gitfacts.Budget()
    if not gitfacts.is_repo(root, budget):
        return None
    state = gitfacts.unfinished(root, budget)
    if not state:
        return None
    return {
        "admit": False,
        "reason": f"{root.name} has {REASONS.get(state, state)} to resolve",
        # Carried for a reader of the ledger who wants to know which check
        # fired without parsing the sentence above. Nothing in the kernel reads
        # it, and nothing should have to.
        "state": state,
    }
