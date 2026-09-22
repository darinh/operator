---
name: pr-gate
description: "What must pass before opening a PR here, and when an agent may merge its own. Use for 'open a PR', 'can I merge this', 'self-merge', 'is this ready to merge', 'land this', or 'ship it'. Also fires before declaring a change done."
---

# PR gate

Three gates, in order. All three pass, or open the PR and hand it back instead.

Owner's terms, 2026-09-21: "The gate is code test coverage and you need 2 frontier model agents
of different families to review your PRs for you, in case you have blind spots. If the 2 agents
approve, everything builds, and your changes have test coverage, then you can self-merge."

## Gate 1, mechanical

Run `python .github/skills/pr-gate/preflight.py --pr <number>` and fix what it reports. It owns
the checks that need no judgement.

**Always pass `--pr`.** Without it the CI check reads local `HEAD`, which may no longer be the
PR head, so it certifies a SHA that is not the one merging.

| Check | Why |
| --- | --- |
| Working tree clean | An uncommitted file is not in the PR and nobody reviews it |
| Full suite green | `python -m pytest -q` from the repo root |
| Every changed source **changed its test file too** | Existence is too weak. Adding two hundred lines to a module whose test was written a year ago would otherwise pass without one new assertion |
| A new kernel module appears in `tests/op.py` `_MODULE_NAMES` | Absent, the suite's monkeypatching silently misses it, which has bitten this repo before |
| No budget ceiling moved silently | Raising one blocks the gate until you pass `--budget-raised "<reason>"`, which prints the reason so it lands in the record rather than in nobody's memory |
| CI concluded success on the exact head SHA | Not "auto-merge armed", see below |

`--skip-tests` exists for iterating. It reports the gate as incomplete and exits non-zero, so it
is never a way to print a pass.

**When the script cannot read the diff it fails, it does not pass.** `changed_files` returns
`None` rather than an empty list on a git error, and every check that depends on it refuses. A
gate that passes because it could not see is worse than no gate.

**What this does not do.** It does not measure line coverage, and co-changing a test file is not
a substitute for it. Requiring `tests/test_foo.py` in the same diff as `foo.py` establishes one
thing only, that both paths appeared in `git diff --name-only`. It does not establish that the
test gained an assertion, or that any test exercises the line that moved. A blank line appended
to the test file satisfies it. Question 6 below is what actually carries coverage here, and it
wants evidence rather than a number.

**The CI trap.** `gh pr merge --auto` merges as soon as the PR is mergeable. If no workflow run
exists yet for the head SHA there is nothing to wait on, so it merges unchecked. That happened
on #17 on 2026-09-20. Assert a run exists for the exact SHA and concluded success. A green run
on an earlier SHA proves nothing, because a rebase makes a new one.

## Gate 2, two reviewers from different families

Spawn two reviewers on different model families. Both must approve.

**Split their briefs.** A shared brief makes them converge and the second one teaches you
nothing. The split that has worked here:

- Reviewer A falsifies the PR description's claims one by one against the diff.
- Reviewer B finds what the diff omits, and anything claiming more than it checks.

Findings are leads, not verdicts. Check the cited file and line before acting. On 2026-09-20 a
review of this repo returned six findings. Four were real. Two had the mechanism wrong while
pointing at a genuine problem underneath.

"No significant issues found" is not an approval until you confirm it read the diff.

## Gate 3, the questions

Answer each in the PR description or a comment. "No" is fine. Silence is not, because noticing
is the point.

0. **What authorised this?** Name the human request, issue, or approved item this traces to. An
   agent that satisfies every mechanical check while doing work nobody asked for is the failure
   `docs/plan.md` section 3.2 exists to prevent, and no other question here would catch it.
1. Does this change a contract something else depends on? Ledger record shape, exit codes, file
   names, console script names, a seam another package reaches through.
2. Which documents name the behaviour I changed? On 2026-09-20 `docs/ledger.md` claimed a
   detection the code did not make.
3. Does the PR description claim anything the code does not do? Read it against the diff as if
   someone else wrote it.
4. Did I raise a budget or threshold to make something fit? Name it and say why that beat
   cutting. This repo records the opposite failure, where the cheapest way to pass was to
   delete explanation.
5. Is there a test or metric that now claims more than it checks? A name promising timeliness
   while the assertion only reads an exit code is the shape.
6. Does each new test fail without the change? Run it against the unfixed source and keep the
   output. A test passing both ways proves nothing.
7. Does this need a guard so it cannot silently regress? If the same correction has come up
   twice, encode it instead of writing it down again.
8. Is anything Windows-only or Linux-only? CI is both. `Popen._wait` busy-waits on POSIX and
   blocks on Windows, which already cost a full round trip. WSL reproduces Linux faster than CI.
9. Does this add a command, flag, or entry point? The owner wants one `operator` entry point
   with a menu, not a family of binaries somebody has to memorise.
10. What did I learn that belongs in this list? Add it.

## Merging

All three gates passed, then `gh pr merge <n> --rebase`. This repo allows only rebase merges
and auto-deletes branches.

**A rebase merge makes a SHA that CI never saw.** If `main` moved since the branch went green,
the rebased commit landing on `main` is new. Check the post-merge run on `main` and be ready to
revert, or rebase onto current `main` and let CI go green there first, which is the cheaper
habit. Verifying `main` after a merge is not optional, because nothing else does it.

**Record the Gate 2 reviews where a human can see them**, with `gh pr review` or a PR comment
quoting each reviewer's verdict and model. A review that exists only in an agent's context did
not happen as far as any reader is concerned.

**Do not stack PRs.** Rebase-merging rewrites the SHAs above it, so each land forces a rebase
cascade on everything stacked on top. One combined PR instead. Large PRs are fine here.

## Growing this file

Question 10 is the mechanism. When something gets missed, add the question that would have
caught it. Where a check can replace a question, write it into `preflight.py` instead.

Do not delete a question because it keeps being answered "no". A question about an invariant
that still holds is doing its job; that is what maintained looks like.
