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

#: Caps on how much of a configured tree is examined at all. Discovery calls
#: git once per candidate directory, so a root with four hundred children is
#: four hundred subprocesses -- and a reviewer found that discovery ran with no
#: budget at all, which made `roots: ["~/repos"]` on a cold filesystem a
#: reliable way to overrun the worker deadline and quarantine this extension
#: for the life of the fleet host. The budget is the real defence; these are
#: the cheap one that stops the budget being spent before the scan starts.
MAX_ROOTS = 20
MAX_CHILDREN = 50


def _repos(config, budget: gitfacts.Budget) -> "list[Path]":
    """Configured roots, expanded to the repositories under them.

    A root that is itself a repository is used directly; otherwise its
    immediate children are examined. Nothing recurses, and a root that is
    neither is silently skipped -- a hand-edited path that no longer exists is
    an ordinary thing to find in a config file and not worth a failure.

    The budget is checked before every git call, including the ones that only
    decide whether a directory is a repository. Discovery used to run outside
    it entirely, which meant the scan could be over its deadline before the
    first worktree was looked at.
    """
    found: list[Path] = []
    for root in activation.roots(config)[:MAX_ROOTS]:
        if budget.spent():
            break
        try:
            if not root.is_dir():
                continue
        except OSError:
            continue
        if gitfacts.is_repo(root, budget):
            found.append(root)
            continue
        if SCAN_DEPTH < 1:
            continue
        try:
            children = sorted(child for child in root.iterdir()
                              if child.is_dir())[:MAX_CHILDREN]
        except OSError:
            continue
        for child in children:
            if budget.spent():
                break
            if gitfacts.is_repo(child, budget):
                found.append(child)
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

    # The primary checkout, which is never proposed for *removal* -- it is the
    # repository -- but which is also the workdir a seat is usually pointed at.
    # A reviewer found that skipping it entirely broke the pairing
    # `worktree_guard` documents: the guard refuses a launch into a
    # half-finished merge in the main checkout forever, and nothing put the
    # reason on the queue a human drains, so the seat went quiet with no
    # explanation anywhere a person looks. Asked of `repo` directly rather than
    # of `trees[0]`, so a porcelain listing that failed to parse cannot take
    # this check down with it.
    state = gitfacts.unfinished(repo, budget)
    if state:
        out.append((
            f"{repo.name}:{repo}:{state}",
            f"{repo.name}: the main checkout has an unfinished {state}",
            f"{repo} is part-way through a {state}. A seat pointed at it will "
            f"not be admitted while that is true, so this needs a person to "
            f"finish or abort it.",
        ))

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
            f"already contains, and git reports no uncommitted changes. "
            f"`git worktree remove` would retire it. Check for ignored files "
            f"first -- a .env or a build directory is invisible to the check "
            f"behind this proposal and to the removal itself.",
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
    for repo in _repos(config, budget):
        if budget.spent():
            break
        seen.extend(_findings(repo, integration, budget))

    state = activation.read_state(NAME)
    already = state.get("proposed")
    already = set(already) if isinstance(already, list) else set()
    fresh = [item for item in seen if item[0] not in already]
    returned = fresh[:MAX_PER_CALL]

    # Remembered: what was still found *and* was already known, plus what is
    # actually being handed back now. The first version remembered everything
    # the scan saw, which silently buried findings six and beyond -- they were
    # marked proposed by a call that never returned them, so they were never
    # proposed at all. A reviewer found it by putting seven stale worktrees in
    # front of it and getting five, then None.
    #
    # Dropping a key that has left the scan is the other half, and it is why
    # this is not a union with `already`: a finding that has been dealt with
    # falls out of the memory too, so the same worktree going bad again is
    # proposed again rather than suppressed forever by a record of the first
    # time.
    #
    # KNOWN LIMITATION, and two reviewers found it independently: this advances
    # before `FleetHost` appends. An extension is one process per call with no
    # acknowledgement channel, so a proposal returned into a queue that then
    # refuses the append (`QueueUnwritable`, past 4 MB) is remembered as told
    # and will not be repeated. The host reports that failure to
    # `fleet-failures.jsonl`, which is where the loss is visible; closing it
    # properly needs a reply the hook contract does not have.
    still_seen = {key for key, _, _ in seen}
    remembered = (already & still_seen) | {key for key, _, _ in returned}
    activation.write_state(NAME, {"proposed": sorted(remembered)})

    return [{"title": title, "detail": detail}
            for _, title, detail in returned] or None
