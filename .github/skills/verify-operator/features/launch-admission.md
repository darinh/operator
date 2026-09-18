# Launch admission (kernel hook)

Before a seat starts a session, the supervisor asks every enabled extension
whether it may. An extension can refuse, and the refusal is honoured: the seat
stays closed and the reason goes to the ledger. This is the hook that makes
extensions load-bearing rather than advisory, and it is the reason they ship
inert — an extension that started answering on `pip install` would be one
install away from holding every seat in the fleet shut.

It is a **kernel** hook, not a fleet one. `operator-fleet` lists
`worktree-guard` at discovery and never calls it: the fleet hooks (`on_fact`,
`on_tick`, `propose_work`) and the kernel hooks are disjoint sets. So this
feature is unreachable through the two console scripts, and
`control_operator.py gate` drives the kernel's own `extension_seam.launch_gate`
factory instead — the same one the supervisor uses.

## Sub-features

- `gate-inert` an installed but unenabled extension refuses nothing.
- `gate-refuse` an enabled extension's refusal is returned, with its reason.
- `gate-attribute` a refusal names the extension that made it.
- `gate-recover` the refusal lifts when the condition it named is gone.
- `gate-blind` an extension that could not answer is recorded, not guessed at.
- `gate-fail-open` an absent, broken or silent extension admits.

## How to get to it (user POV)

- Nobody invokes this directly. It runs inside `run_loop_mode` before each
  launch, so a user reaches it by running a supervised seat in a repository an
  extension objects to.
- The observable effect is a seat that does not start, and a `claim.*` record in
  `trace.jsonl` naming who refused.

## Driving it with control_operator

Preconditions:

- `up` has run and `doctor --run <run>` reports `doctor: healthy`.
- A repository stuck in an unfinished git operation. A conflicted merge is the
  cheapest: commit a file on `main`, commit a different version of it on another
  branch, then `git -C <repo> merge <branch>` and leave the conflict alone.
  Confirm with `Test-Path <repo>/.git/MERGE_HEAD`.

- **Prove inert admits.** With no `extensions.json`, run
  `control_operator.py gate --run <run> --label inert --workdir <repo>`. stdout
  is `admit: True` with no refusals — even though the repository is mid-merge and
  `worktree-guard` is installed and would object. Registration is not activation,
  and this is the hook where that distinction is load-bearing.
- **Enable the guard.** Run
  `control_operator.py enable --run <run> --extension worktree-guard`. It takes
  no settings.
- **Prove the refusal.** Run the gate again. stdout is `admit: False` followed by
  `refused by worktree-guard: repo has an unfinished merge to resolve`. The
  refusal names the extension and states the condition; `admit` is a property
  derived from the refusals, so nothing can report a yes while carrying one.
- **Prove it lifts.** Resolve the repository with `git -C <repo> merge --abort`,
  then run the gate a third time with the extension **still enabled**: `admit:
  True`. The only thing that changed between the refusal and the admission is the
  state of the repository, which is what makes the pair a proof rather than a
  coincidence.
- **Prove fail-open.** Point `--workdir` at a directory that is not a repository
  at all, with the guard still enabled: `admit: True`. An extension with no
  opinion returns `None` rather than an admission, because having checked one
  condition is no basis for asserting a launch is fine.
- **Proof.** `artifacts/transcript.md` carries each gate call with its exit code
  and stdout, so the three-way sequence reads as one record.

## Gotchas

- **This is not `operator-fleet`.** Enabling `worktree-guard` and running fleet
  rounds forever will never call it. If a drive seems to show the guard doing
  nothing, check which host is being asked.
- **`worktree-guard` takes no settings**, unlike `seat-watch` and
  `worktree-janitor`. `{"worktree-guard": {"enabled": true}}` is the whole
  configuration.
- **It needs `workdir` in the facts.** The gate serialises whatever the call site
  passes; without a `workdir` string the extension returns `None` and the launch
  is admitted. A refusal that does not appear may be a missing fact rather than a
  working repository.
- **Fail-open is an absence, not a policy.** An extension that crashed, hung, or
  was never discoverable refuses nothing. A launch you want *stopped* needs a
  kernel check, not an extension.
- **A quarantined extension stays quarantined for the life of the gate.** The
  host is built once per run precisely so that a deadline overrun is not paid
  again every session, so within one process a slow extension is asked once.
- **The reason reaches the ledger and never a session's prose.** It is written
  for a human reading `trace.jsonl`. Do not expect to find it in a preamble.
- **Repeated identical states are deduplicated** in the ledger, so a window that
  stays shut is one record rather than one per attempt. Absence of a second
  record is not absence of a second refusal.
