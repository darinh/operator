# operator verification map

This directory is the maintained source for verifying the user-facing behaviour of
`operator`. Read this index before driving, then use the matching feature file as
the recipe.

**The three core features need no extensions.** A core install with an empty
`extensions.json` — or none at all — can be fully verified. Extensions are an
optional layer with its own file.

## Baseline preconditions

- Create a disposable instance with `python .github/skills/verify-operator/control_operator.py up`.
- Keep the printed `run` path. Every later command takes it as `--run <run>`.
- Run `doctor --run <run>` and require `doctor: healthy` with exit `0`.
  `extensions enabled: none (core only)` is a normal reading.
- Never drive the real `~/.operator`. `doctor` asserts the home is elsewhere; if
  that check ever fails, stop rather than continue.
- Never drive an instance this run did not create with `up`.

## Driving conventions

- Start every recipe from a fresh `up` unless its preconditions say otherwise.
- Pass console-script flags after `--`; they reach the command untouched.
- Treat every command as literal. Keep quoted text, kinds and flags unchanged.
- Fleet actions go through `control_operator.py fleet --run <run> -- <args>`.
- Seat actions go through `control_operator.py seat --run <run> -- <args>`.
- Give every call a `--label`, because the label names its transcript entry.
- Identify things by stable handles: ledger records by `event` and `instance`,
  proposals by the attributed extension name, journal entries by the 8-character id.
- **Seed fresh ledger records for every round you expect to produce something.**
  The tail advances on every round, enabled or not, and never redelivers.
- Do not remove proof artifacts during cleanup. `down` already leaves them.

## Fixtures versus user paths

`seed-ledger`, `seed-queue` and `enable` are fixture setup. They stand in for a
supervisor writing the ledger, an extension writing a proposal, and a human
writing the activation file. The behaviour under test is always what the **shipped
console script** does with that state, never the seeding itself. Say which is
which when reporting a proof.

## Proof and skip reporting

- Exercise the installed console script, never the Python function behind it.
- Capture the user action and the resulting on-disk state, not just stdout.
- Snapshot with `evidence --label <name>` on **both sides** of a mutation; the
  difference between the two labels is the proof.
- CLI proof includes the command, stdout, stderr and exit code. `transcript.md`
  records all four automatically.
- Report an unreachable path with the attempted command and the unmet
  precondition. Do not report a skipped entry point as verified via another path.

## Feature entry contract

Each feature file starts with an H1 and one paragraph of user-visible behaviour,
then exactly four H2 sections in this order:

1. `Sub-features` — short IDs, one line each.
2. `How to get to it (user POV)` — every user entry point.
3. `Driving it with control_operator` — starts with `Preconditions:`, then
   labelled bullets pairing a user action with an exact command and an observable
   result.
4. `Gotchas` — traps that waste or invalidate a run.

Keep implementation detail out of the map. Name user paths, stable handles,
required state, commands and observable proof.

## Core features

No extension required for any of these.

- [Seat memory](./seat-memory.md) — `operator-seat remember` / `recall` /
  `forget`, the authority-vetting envelope, and supersession.
- [Proposal queue](./proposal-queue.md) — `operator-fleet proposals`, the
  rename-claim drain, the archive, and abandoned-batch recovery.
- [Fleet host](./fleet-host.md) — `operator-fleet run`, rounds, discovery,
  inert-by-default, the ledger cursor, and isolation.

## Optional layer

- [Extensions](./extensions.md) — the activation gate, plus `seat-watch` and
  `worktree-janitor` as worked examples of verifying an extension end to end.
  Read this only when changing an extension or the gate itself.
- [Launch admission](./launch-admission.md) — the **kernel** hook
  `admit_launch`, where an extension's refusal holds a seat closed. Unreachable
  through the two console scripts; driven through `control_operator.py gate`.

## Coverage gaps

None outstanding. Every feature the map names is driven against real code.

Recorded here so a later run knows these were checked rather than assumed: the
4 MB queue refusal, the 4 MB journal refusal, the 600-character entry
truncation, the `fleet.stop` marker, following a ledger rotation without losing
records, `worktree-janitor` proposing a merged worktree while protecting a dirty
one, and a `worktree-guard` refusal holding a launch and then lifting.

The one thing this skill still does not drive is the supervisor loop itself —
`run_loop_mode`, its relaunch behaviour and its breakers. `gate` reaches the
admission hook the loop calls, but not the loop. That needs a multiplexer and a
stub agent, which is a harness of a different kind rather than a missing recipe.
