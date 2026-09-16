# Fleet host (core)

`operator-fleet run` is the process that watches a fleet. Each round it tails the
ledger for records written since last time, hands the new batch to every **enabled**
extension, asks what work they would propose, and appends what comes back to the
NEEDS-HUMAN queue. It decides nothing.

Everything in this file is provable on a core install with **no extensions
enabled**. What an enabled extension then does with a batch is
[`extensions.md`](./extensions.md).

## Sub-features

- `fleet-rounds` stops after `--rounds N` instead of polling forever.
- `fleet-discovery` reports which extensions are installed, at startup.
- `fleet-inert` proposes nothing when nothing is enabled.
- `fleet-tail` records a cursor and resumes from it; it never redelivers.
- `fleet-isolate` writes only under the home it was given.
- `fleet-stop` ends a `--rounds`-less run when `fleet.stop` appears.

## How to get to it (user POV)

- Run `operator-fleet run` to poll until stopped.
- Run `operator-fleet run --rounds N` to stop after N polls.
- Run `operator-fleet --home <dir> run` to watch a specific state directory.
- Run `operator-fleet run --interval S` to change the seconds between polls.
- Create `<home>/fleet.stop` to end a run that has no `--rounds`.

## Driving it with control_operator

Preconditions:

- `up` has run and `doctor --run <run>` reports `doctor: healthy`.
- `doctor` reports `extensions enabled: none (core only)`.

- **Run one round.** Run
  `control_operator.py fleet --run <run> --label round-one -- run --rounds 1 --interval 0.1`.
  Exit `0`. stdout reads `fleet host watching <home>`, `stopping after 1 round(s)`
  and `fleet host stopped after 1 round(s)`.
- **Prove discovery.** The same call's **stderr** carries
  `Extensions watching the fleet: ...` naming every registered entry point. This
  appears even when none are enabled: discovery is not activation, and the list
  reflects what `pip` installed, not what a human turned on.
- **Prove inert by default.** Seed a ledger record, run a round, and read the
  queue. Run
  `control_operator.py seed-ledger --run <run> --record '{"event":"session_exit","instance":"verify-seat","consecutive":3}'`,
  then `control_operator.py fleet --run <run> --label round-inert -- run --rounds 1 --interval 0.1`,
  then `control_operator.py fleet --run <run> --label queue-inert -- proposals`.
  The queue reports `no proposals waiting`. Installing a package changed nothing.
- **Prove the tail advanced anyway.** Run
  `control_operator.py evidence --run <run> --label after-inert`. The manifest
  lists `fleet-tail.json`; read it and note its `offset` equals the size of
  `trace.jsonl`. The inert round consumed the record. This is the single most
  important fact in this file.
- **Prove it never redelivers.** Run a second round with no new records and read
  the queue again: still `no proposals waiting`, and the offset is unchanged.
  Records consumed while nothing was enabled are gone to the fleet forever.
- **Prove the cursor is keyed on file identity.** `fleet-tail.json` carries an
  `identity` pair alongside the offset. The ledger rotates by **rename**, so a
  replacement file must be detected by identity rather than by being shorter than
  the offset — a longer replacement would defeat a size check silently.
- **Prove isolation.** After any round, confirm the real `~/.operator` is
  unchanged (compare `operator.log` size before and after), while
  `<run>/home/operator.log` exists and has grown. The run wrote only to its own
  home.
- **Prove the stop marker.** Start a run with no `--rounds` in the background,
  create `<home>/fleet.stop`, and confirm the process exits `0` reporting the
  rounds it completed. Prefer `--rounds` for every other drive.
- **Proof.** `artifacts/transcript.md` carries each round with its exit code and
  both streams; `artifacts/after-inert/` carries the ledger and the tail cursor.

## Gotchas

- **The tail never redelivers, and every round advances it** — including a round
  with nothing enabled. An inert proof and an enabled proof cannot share one batch
  of seeded records. Seed again after enabling, or use two separate `up` runs.
- **The discovery line goes to stderr, not stdout.** A check that reads only
  stdout will miss it.
- **Discovery lists what is installed, not what is enabled.** Seeing three
  extension names in the log says nothing about whether any of them can answer.
- **Always pass `--rounds`.** Without it the host polls until `<home>/fleet.stop`
  appears, and the default interval is 15 seconds. `--interval 0.1` keeps a
  multi-round drive quick.
- **`--home` is a flag on `operator-fleet` itself, before the verb**, so it is
  `operator-fleet --home <dir> run`, not `operator-fleet run --home <dir>`.
- **The home reaches extension workers through the environment.** The CLI exports
  `COPILOT_OPERATOR_HOME` so spawned workers resolve the same directory. A
  hand-rolled call that sets only `--home` can have workers read the developer's
  real home.
- **An extension that overran its deadline is quarantined for the life of the
  host**, so within one `run --rounds N` a slow extension is not retried each
  round.
- **A proposal is not work.** Nothing in this path can mint an assignment or an
  approval. If a drive appears to create one, that is the finding.
