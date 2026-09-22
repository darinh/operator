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

Run `python .github/skills/pr-gate/preflight.py` and fix what it reports. It owns the checks
that need no judgement.

| Check | Why |
| --- | --- |
| Working tree clean | An uncommitted file is not in the PR and nobody reviews it |
| Full suite green | `python -m pytest -q` from the repo root |
| Every changed source file has a matching test file | `foo.py` needs `tests/test_foo.py`, the rule `test-enforcer` already applies per commit |
| A new kernel module appears in `tests/op.py` `_MODULE_NAMES` | Absent, the suite's monkeypatching silently misses it, which has bitten this repo before |
| CI concluded success on the exact head SHA | Not "auto-merge armed", see below |
| No line budget raised without saying so | Raising one is allowed as a stated decision, never as a side effect |

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

**Do not stack PRs.** Rebase-merging rewrites the SHAs above it, so each land forces a rebase
cascade on everything stacked on top. One combined PR instead. Large PRs are fine here.

## Growing this file

Question 10 is the mechanism. When something gets missed, add the question that would have
caught it. Where a check can replace a question, write it into `preflight.py` instead. A
question never once answered "yes" is a candidate for deletion.
