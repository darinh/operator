"""Propose worktree tidying to a human. Deletes nothing, ever.

The state this looks for is the state `operator worktree recover` was written
for in the system this kernel is extracted from: a checkout whose branch landed
weeks ago, a registration whose directory is gone, a tree stuck mid-merge. Each
one is cheap to spot and expensive to leave, and none of them is safe to act on
automatically -- a worktree with uncommitted work in it looks identical to one
that is finished, right up until somebody loses an afternoon.

So this proposes, and the kernel disposes, and between the two stands a human
minting an approval. That is INV-WORK, and here it is the shape of the module
rather than a check somebody remembers to run: `gitfacts` contains no mutating
git verb, so there is no code path from a proposal to a deletion even if this
file wanted one.

**A proposal is not a reminder.** `propose_work` is asked on every tick, so the
naive implementation re-proposes the same worktree every five minutes until the
queue hits its 4 MB ceiling and starts refusing appends -- and the queue refuses
rather than rotating precisely because nobody drains it yet. What is proposed is
therefore remembered, keyed by repository and worktree, and proposed again only
if it disappears from the scan and comes back. `activation.state_path` holds
that, because one process per call means there is nowhere else to hold it.

**Scanning is bounded and gives up quietly.** Nine repositories on a cold
filesystem can exceed a worker's ten-second deadline, and a worker killed at its
deadline is quarantined for the life of the fleet host -- so the extension that
overran once never answers again. `gitfacts.Budget` stops the scan while there
is still time to reply, and an unfinished scan proposes what it found rather
than nothing.
"""
from __future__ import annotations

from pathlib import Path

from . import activation, gitfacts

NAME = "worktree-janitor"

#: The branch a landed change is expected to be contained by. Overridable per
#: configuration because not every repository calls it `main`.
DEFAULT_INTEGRATION = "main"

#: The most this extension will propose in one call, regardless of what it
#: found. The fleet host caps this too, and reports a `TooManyProposals`
#: failure when it does; being the second thing to enforce it means a fleet
#: waking up to forty stale worktrees puts the interesting ones in front of a
#: human instead of a truncation notice.
MAX_PER_CALL = 5

#: How many directories deep to look for repositories under a configured root.
#: One: a root is either a repository or a directory of them. Walking further
#: turns a misconfigured `roots: ["~"]` into a filesystem crawl on the fleet's
#: poll interval.
SCAN_DEPTH = 1


def _repos(config) -> "list[Path]":
    """Configured roots, expanded to the repositories under them.

    A root that is itself a repository is used directly; otherwise its
    immediate children are examined. Nothing recurses, and a root that is
    neither is silently skipped -- a hand-edited path that no longer exists is
    an ordinary thing to find in a config file and not worth a failure.
    """
    found: list[Path] = []
    for root in activation.roots(config):
        try:
            if not root.is_dir():
                continue
        except OSError:
            continue
        if gitfacts.is_repo(root):
            found.append(root)
            continue
        if SCAN_DEPTH < 1:
            continue
        try:
            children = sorted(child for child in root.iterdir()
                              if child.is_dir())
        except OSError:
            continue
        found.extend(child for child in children if gitfacts.is_repo(child))
    return found


def _findings(repo: Path, integration: str,
              budget: gitfacts.Budget) -> "list[tuple[str, str, str]]":
    """`(key, title, detail)` for everything worth a human's attention in `repo`.

    The first entry from `git worktree list` is the main checkout and is never
    proposed for removal: it is the repository. Only linked worktrees are
    candidates, which is also why this cannot be driven off a directory listing.
    """
    out: list[tuple[str, str, str]] = []
    trees = gitfacts.worktrees(repo, budget)
    for tree in trees[1:]:
        if budget.spent():
            break
        if tree.locked:
            # Locked is a human saying "leave this alone" in git's own
            # vocabulary. Proposing it anyway would be arguing with the lock.
            continue
        key = f"{repo.name}:{tree.path}"
        if tree.prunable:
            out.append((
                f"{key}:prunable",
                f"{repo.name}: worktree registration for "
                f"{Path(tree.path).name} has no directory",
                f"git worktree list reports {tree.path} as prunable, which "
                f"means the checkout is gone but its registration remains. "
                f"`git worktree prune` in {repo} clears it.",
            ))
            continue
        state = gitfacts.unfinished(tree.path, budget)
        if state:
            out.append((
                f"{key}:{state}",
                f"{repo.name}: {Path(tree.path).name} has an unfinished "
                f"{state}",
                f"{tree.path} is part-way through a {state}. A seat pointed "
                f"at it will not be admitted while that is true, so this "
                f"needs a person to finish or abort it.",
            ))
            continue
        if not tree.branch or tree.detached:
            continue
        if not gitfacts.is_merged(repo, tree.branch, integration, budget):
            continue
        dirty = gitfacts.has_changes(tree.path, budget)
        if dirty is not False:
            # True means real uncommitted work; None means the question could
            # not be asked. Neither is grounds to suggest deleting a directory,
            # and collapsing them would make an unreadable checkout look tidy.
            continue
        out.append((
            f"{key}:merged",
            f"{repo.name}: worktree {Path(tree.path).name} is merged into "
            f"{integration}",
            f"{tree.path} is on branch {tree.branch}, which {integration} "
            f"already contains, and the tree has no uncommitted changes. "
            f"Retiring it with `git worktree remove` would lose nothing.",
        ))
    return out


def propose_work(**facts):
    """Everything the janitor thinks a human should look at, proposed once each.

    Returns a list of `{"title", "detail"}`, which is the only shape
    `FleetHost._vetted` accepts -- anything else is reported as a `BadProposal`
    against this extension by name, which is the right outcome and worth not
    triggering.
    """
    config = activation.settings(NAME)
    if config is None:
        return None
    integration = config.get("integration")
    if not isinstance(integration, str) or not integration.strip():
        integration = DEFAULT_INTEGRATION

    budget = gitfacts.Budget()
    seen: list[tuple[str, str, str]] = []
    for repo in _repos(config):
        if budget.spent():
            break
        seen.extend(_findings(repo, integration, budget))

    state = activation.read_state(NAME)
    already = state.get("proposed")
    already = set(already) if isinstance(already, list) else set()
    fresh = [item for item in seen if item[0] not in already]

    # Remembered against what this scan *saw*, not against the union with what
    # was remembered before. A finding that has been dealt with drops out of
    # the scan and out of the memory with it, so the same worktree going bad
    # again is proposed again rather than being suppressed forever by a record
    # of the first time.
    activation.write_state(NAME, {"proposed": sorted(key for key, _, _ in seen)})

    return [{"title": title, "detail": detail}
            for _, title, detail in fresh[:MAX_PER_CALL]] or None
