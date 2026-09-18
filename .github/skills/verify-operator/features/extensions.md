# Extensions (optional layer)

**Nothing in this file is needed to verify core operator.** It is here for the
case where you are changing an extension, or changing the activation gate itself.
The core features — [seat memory](./seat-memory.md), [proposal
queue](./proposal-queue.md), [fleet host](./fleet-host.md) — are all provable with
no extension enabled.

Extensions ship registered but inert. Installing a package must not change how the
fleet behaves: `admit_launch` sits on the launch path of every seat and its
refusals are honoured, so an extension that started answering the moment `pip`
finished would be one install away from holding every seat closed. A human turns
each one on by writing `<home>/extensions.json`, and **every** failure to read that
file means "not enabled".

`control_operator.py` knows nothing about any specific extension. `enable` takes a
name and settings; `seed-ledger` takes whatever record shape the extension reads.
Verifying a new extension means writing a recipe here, not changing the helper.

## Sub-features

- `act-inert` a freshly installed extension answers nothing.
- `act-enable` an entry with `"enabled": true` makes exactly that one answer.
- `act-settings` extra keys in the entry reach the extension as configuration.
- `act-missing` a missing config file means off, silently and safely.
- `act-corrupt` an unparseable config file means off, not a guess.
- `act-shape` an entry of the wrong shape means off.
- `act-scope` activation is per-home, so enabling here cannot affect other repos.
- `act-state` an enabled extension keeps cumulative memory in `extensions/<name>.json`.
- `act-janitor` a second extension, driven from the filesystem rather than the ledger.

## How to get to it (user POV)

- Write `~/.operator/extensions.json` by hand:
  `{"seat-watch": {"enabled": true, "failures": 3}}`.
- Point `COPILOT_OPERATOR_HOME` elsewhere to use a different config.
- Delete the file, or the entry, to turn an extension back off.

## Driving it with control_operator

Preconditions:

- `up` has run and `doctor --run <run>` reports `doctor: healthy`.
- The run's home has no `extensions.json` yet.

The worked example below uses `seat-watch`, which reads `session_exit` records and
proposes when a seat has ended too many sessions unexplained. Its record shape and
its `failures` setting are **its** contract, not the harness's — another extension
needs a different `seed-ledger` payload and its own section here.

- **Prove inert on install.** Seed three rising records and run a round with no
  config file:
  `control_operator.py seed-ledger --run <run> --record '{"event":"session_exit","instance":"verify-seat","session":1,"consecutive":1,"limit":5,"markers":{},"giving_up":false}' --record '{"event":"session_exit","instance":"verify-seat","session":2,"consecutive":2,"limit":5,"markers":{},"giving_up":false}' --record '{"event":"session_exit","instance":"verify-seat","session":3,"consecutive":3,"limit":5,"markers":{},"giving_up":false}'`,
  then `control_operator.py fleet --run <run> --label inert -- run --rounds 1 --interval 0.1`,
  then `proposals`. stdout is `no proposals waiting`.
- **Prove discovery is not activation.** That round's stderr still lists every
  registered extension. Being listed is not being enabled.
- **Enable one.** Run
  `control_operator.py enable --run <run> --extension seat-watch --setting failures=3`.
  stdout confirms `{"enabled": true, "failures": 3}`.
- **Seed again.** The inert round consumed the first batch and the tail does not
  redeliver, so repeat the three `--record` arguments above. Skipping this makes
  the next step report nothing and look like a broken extension.
- **Prove it now answers.** Run
  `control_operator.py fleet --run <run> --label enabled -- run --rounds 1 --interval 0.1`
  then `proposals`. One proposal attributed `seat-watch` is waiting, reading
  `seat verify-seat has ended 3 sessions unexplained`. Only the enabled extension
  answered.
- **Prove settings reach it.** In a **fresh** run, enable with `--setting
  failures=3`, seed only two records (`consecutive` 1 then 2), and run a round: no
  proposal, and `extensions/seat-watch.json` shows `"consecutive": 2`. Now change
  only the setting — `--setting failures=2` — and run another round **without
  seeding anything**. The proposal appears. `propose_work` reads the state file
  rather than the ledger, so nothing but the configured number changed. That is
  what makes it a proof rather than a coincidence.
- **Prove a corrupt config means off.** Overwrite the file with `{not json`, seed
  a fresh batch, run a round: no proposal, and the command still exits `0`.
- **Prove a malformed entry means off.** Replace it with `{"seat-watch": {}}`,
  seed another batch, run a round: still nothing. Then enable properly, seed once
  more, and run: the proposal appears. Those three outcomes in one run are the
  activation contract.
- **Prove the scope.** Confirm the real `~/.operator/extensions.json` is still
  absent (or unchanged). Enabling an extension for a verification run must not
  enable it for every repository on the machine.
- **Proof.** `artifacts/transcript.md` carries the inert round and the enabled
  round with identical commands and different outcomes.

### A second extension: `worktree-janitor`

It implements `propose_work` only, needs no ledger records at all, and reads the
filesystem instead — so it exercises a different half of the contract than
`seat-watch` does. It needs a real repository with a merged worktree, which is
cheap to build:

```
git init -b main -q <tmp>/proj
git -C <tmp>/proj commit -q -m base          # after adding a file
git -C <tmp>/proj worktree add -q -b feature <tmp>/wt
git -C <tmp>/wt commit -q -m "feature work"  # after adding a file
git -C <tmp>/proj merge -q --no-ff feature -m "merge feature"
```

- **Enable it against that root.** Run
  `control_operator.py enable --run <run> --extension worktree-janitor --setting "roots=[\"<tmp>/proj\"]"`.
  Forward slashes, because the value is parsed as JSON.
- **Prove it proposes a retired worktree.** Run one round, then `proposals`. The
  queue holds a `worktree-janitor` record reading
  `proj: worktree wt is merged into main`, whose detail names the branch, states
  that git reports no uncommitted changes, and warns that ignored files are
  invisible both to the check and to the removal.
- **Prove it protects uncommitted work.** Write an untracked file into `<tmp>/wt`
  and run the same thing in a **fresh** run: `no proposals waiting`. Delete the
  file, run a third fresh run: the proposal is back. The only thing that changed
  was whether the worktree was clean, which is what makes the pair a proof rather
  than a coincidence.
- **It proposes; it never removes.** `gitfacts` contains no mutating git verb and
  `tests/test_extension_packaging.py` asserts their absence over the parsed
  source. A drive that finds a worktree actually deleted is the finding.

## Gotchas

- **Inert and broken look identical from the outside.** Both produce an empty
  queue. Always prove the enabled case in the same run so the pair distinguishes
  them.
- **Seed again after every config change.** The ledger tail advances on every
  round, enabled or not, and never redelivers. Each step above needs its own fresh
  batch; reusing one batch across two rounds proves nothing.
- **`"enabled": true` is required.** A bare `{"seat-watch": {}}` is off, and so is
  `{"seat-watch": true}` — the value must be an object of the right shape.
- **Every read failure means off**: missing file, syntax error, permission denial,
  wrong shape. None of them raise and none of them warn. Do not wait for an error
  message that is never coming.
- **The config is resolved on every call, not captured at import**, so a file
  written after the host starts takes effect on the next round.
- **`seat-watch` reads `consecutive` from the record; it never counts.** Delivery
  is at-least-once, so a redelivered record must not increment anything. A flat
  `consecutive` will never cross the threshold — seed a rising one.
- **One proposal per seat per deterioration.** `extensions/seat-watch.json`
  records `"told": N`; a seat already reported is not reported again until
  `consecutive` exceeds it. A second identical round yielding nothing is correct.
- **At most 5 seats per call and 20 proposals per extension per call.**

## Extensions with no recipe yet

- **`worktree-guard`** (`admit_launch`) is a **kernel** hook on the seat launch
  path. The fleet host lists it at discovery and never calls it — the fleet hooks
  are `on_fact`, `on_tick` and `propose_work`, and the two sets are disjoint. It
  cannot be proven through `operator-fleet` at all; reaching it needs the
  supervisor launch path, which this skill does not drive.
