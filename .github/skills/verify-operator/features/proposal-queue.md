# Proposal queue (core)

`operator-fleet proposals` is what a human reads. It shows what the fleet's
extensions put in front of a person and, with `--drain`, archives what was shown
and empties the queue. Draining is a rename followed by a read, never a read
followed by a truncate, so a proposal appended mid-drain is not filed away unseen.

The queue is a plain JSONL file, so **every path here is provable with no
extensions enabled**: seed a proposal as a fixture, then drive the real command.
Only the *producing* of a proposal needs an extension, and that is
[`extensions.md`](./extensions.md).

## Sub-features

- `queue-show` prints what is waiting and removes nothing.
- `queue-empty` reports an empty queue as a normal answer.
- `queue-drain` shows the batch, appends it to the archive and empties the queue.
- `queue-archive` accumulates every drained batch in `proposals.handled.jsonl`.
- `queue-recover` adopts a batch abandoned by a crashed drain.
- `queue-corrupt` reports an unparseable line instead of dropping it.
- `queue-full` refuses appends past 4 MB rather than rotating, and says so.
- `queue-no-approval` archives without approving; a drain cannot mint work.

## How to get to it (user POV)

- Run `operator-fleet proposals` to read the queue.
- Run `operator-fleet proposals --drain` to archive what it shows.
- Run `operator-fleet --home <dir> proposals` against a specific state directory.

## Driving it with control_operator

Preconditions:

- `up` has run and `doctor --run <run>` reports `doctor: healthy`.
- No extension is required. `doctor` may report `none (core only)`.

- **Empty queue.** Before seeding anything, run
  `control_operator.py fleet --run <run> --label queue-empty -- proposals`. Exit
  `0` and stdout is `no proposals waiting in <home>\proposals.jsonl`. Empty is a
  normal answer, not an error.
- **Seed one proposal.** Run
  `control_operator.py seed-queue --run <run> --extension verify-fixture`. This is
  fixture setup standing in for an extension; the behaviour under test is the
  reading and draining, not the producing.
- **Show without removing.** Run
  `control_operator.py fleet --run <run> --label queue-show -- proposals`. stdout
  shows the record attributed to `verify-fixture`, then
  `1 proposal(s) in <home>\proposals.jsonl` and
  `none removed; pass --drain to archive them`.
- **Prove it removed nothing.** Run the same command again: still `1`. Then
  `control_operator.py evidence --run <run> --label before-drain` — the manifest
  lists `proposals.jsonl` and its size.
- **Prove the envelope.** Every physical line of the proposal body carries an
  `[extension <name>, unverified]` prefix. A line without it is the attribution
  envelope defeated, which is a finding rather than a formatting quirk.
- **Drain.** Run
  `control_operator.py fleet --run <run> --label queue-drain -- proposals --drain`.
  Exit `0`. It prints the record it is archiving, then
  `archived 1 proposal(s) to <home>\proposals.handled.jsonl`. A drain that
  archives without showing is the defect this path exists to prevent.
- **Prove the queue emptied.** Run
  `control_operator.py fleet --run <run> --label queue-after -- proposals`: stdout
  is `no proposals waiting`.
- **Prove nothing was lost.** Run
  `control_operator.py evidence --run <run> --label after-drain` and compare the
  two manifests. `before-drain` lists `proposals.jsonl` at N bytes; `after-drain`
  lists no `proposals.jsonl` and `proposals.handled.jsonl` at **the same N bytes**.
  The record moved; it was not copied and it was not lost.
- **Prove the drain is idempotent.** Drain again: exit `0`, `no proposals
  waiting`, and the archive size is unchanged. A second drain is a no-op, not an
  error and not a duplicate entry.
- **Prove accumulation.** Seed and drain a second time. The archive now holds both
  batches; it is append-only and never rotates.
- **Prove a corrupt line is shown.** Append an unparseable line to
  `proposals.jsonl` and run `proposals`. It prints as
  `! unparseable queue line: <text>`, and it is still counted in the total. A
  corrupt tail must not make a queue look empty.
- **Prove an abandoned batch is adopted.** Simulate a drain that died between its
  rename and its archive. Run
  `control_operator.py seed-queue --run <run> --extension crashed-drain --abandoned`,
  which writes a `proposals.draining.<pid>.<ns>.jsonl` instead of the live queue.
  Seed a normal proposal too, then drain. stdout opens with
  `recovered an abandoned batch: proposals.draining.<pid>.<ns>.jsonl` and the
  archive receives **both** — `archived 2 proposal(s)`. The orphan was not
  stranded and the live queue was not lost to it.
- **Prove the queue refuses rather than rotates when full.** Pad the queue past
  its limit with
  `control_operator.py seed-queue --run <run> --extension filler --pad-to-bytes 4194304`,
  enable an extension, seed enough ledger records to make it propose, and run a
  round. The queue is **byte-identical** afterwards — the proposal was refused,
  not appended and not rotated away — and `fleet-failures.jsonl` gains a record
  naming the extension, the hook and the reason:
  `"error": "QueueUnwritable"`. That is the whole point: rotation would delete
  the oldest proposals, which nobody has read yet, so a full queue says so
  instead and the refusal is reported rather than silent.
- **Proof.** `artifacts/transcript.md` carries each command with its exit code;
  `artifacts/before-drain/` and `artifacts/after-drain/` carry the queue and the
  archive on either side, which together are the proof.

## Gotchas

- **Draining is a rename, not a read-and-truncate.** The queue is renamed to a
  per-process `proposals.draining.<pid>.<ns>.jsonl` and only then read. A test
  asserting "the file was read, then emptied" is asserting the old bug.
- **The rename comes first and the reading comes after.** A proposal appended
  between the two stays in the new live queue for the next drain rather than being
  archived unseen.
- **`proposals.draining.*.jsonl` left behind means a drain crashed mid-flight.**
  It is not garbage: the next `--drain` adopts it by renaming it into its own
  batch and prints `recovered an abandoned batch`. Do not delete these by hand
  mid-run.
- **A failed archive exits `1` and names the file.** The batch stays on disk under
  its draining name for the next drain to adopt. A proposal a human was meant to
  see must not go quiet on an error path.
- **The queue refuses appends past 4 MB rather than rotating**, so an undrained
  fleet stops accepting proposals within a day or two. Rotation would delete the
  oldest proposals, which is worse than saying the queue is full.
- **Archiving is not approving.** `proposals --drain` has no spelling of approval
  in it. Moving a line from one file to another cannot become a lease, and
  approval provenance is mintable only by a human.
- **Windows path separators.** stdout prints native paths, so match on the file
  name rather than on a `/`-separated path.
