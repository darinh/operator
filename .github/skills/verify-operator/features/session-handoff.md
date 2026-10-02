# Session handoff

An operator ends its own session and leaves the next one a record. `operator handoff`
writes a markdown file the next launch finds and announces, then sets the marker
the supervisor polls, so the session is torn down and relaunched. The write comes
first: the supervisor acts on the marker as soon as it sees it, so a marker set
before the file is a restart racing the thing it exists to announce.

## Sub-features

- `handoff-write` stores status, next and context at `projects/<guid>/handoff/<operator>.md`.
- `handoff-restart` sets `restart/<operator>`, the marker `supervisor.py` polls.
- `handoff-checkpoint` `--no-restart` writes the file and leaves the session running.
- `handoff-replace` a second write replaces the first atomically, leaving no `.tmp`.
- `handoff-operator-id` files under the operator **id**, which is what the reader probes with.
- `handoff-unusable-operator` refuses a name that is not one path component.
- `handoff-unregistered` refuses from a directory that is not a project.
- `handoff-usage` refuses a missing or whitespace-only status.
- `handoff-unknown-option` refuses a mistyped flag instead of ignoring it.

## How to get to it (user POV)

- Run `operator handoff --instance <operator> --status "<text>"` in a project, with
  optional `--next`, `--context` and `--no-restart`.
- Pass the operator name positionally: `operator handoff <operator> --status "<text>"`.
- Use `--instance=<operator>` and `--status=<text>` if you prefer joined flags.
- Choose "Hand off to the next session" from the `operator` menu, which prompts
  for the operator name and the status and prints the command before running it.
- Every launch preamble advertises this command, already filled in with that
  operator's id.

## Driving it with control_operator

Preconditions:

- `up` has run and `doctor --run <run>` reports `doctor: healthy`.
- No handoff exists yet for the operator name used below.

- **Refuse outside a registered project.** Run
  `python .github/skills/verify-operator/control_operator.py operator --run <run> --label handoff-unregistered --cwd <some-temp-dir> -- handoff --instance verify-handoff --status "should not land"`.
  Exit `1`, and stderr names the unregistered directory and the fix:
  `this directory is not a registered project` then
  `start an operator in this directory first`.
- **Write one without ending the session.** Run
  `control_operator.py operator --run <run> --label handoff-write -- handoff --instance verify-handoff --status "drove the front door" --next "read it back" --no-restart`.
  Exit `0`, and stdout is `handoff written to <run>/home/projects/<guid>/handoff/verify-handoff.md`.
  No `restart requested` line, because nothing was restarted.
- **Prove it landed, and that nothing was restarted.** Run
  `control_operator.py evidence --run <run> --label after-handoff`. The manifest
  lists `projects/<guid>/handoff/verify-handoff.md` with a non-zero size and
  **no** `restart/verify-handoff`. That pair is the proof of `--no-restart`:
  printing a path is not evidence that the session was left alone.
- **End the session.** Run the same command without `--no-restart` and with a
  different status. Exit `0`, and stdout now carries both lines:
  `handoff written to ...` and `restart requested for verify-handoff`.
- **Prove the marker the supervisor polls is set.** Run
  `control_operator.py evidence --run <run> --label after-restart`. The manifest
  now lists `restart/verify-handoff (0b)` beside the handoff file. The marker is
  empty by design; its existence is the signal.
- **Prove the replace, and that no litter is left.** The handoff file holds the
  second status and not the first, and the `handoff/` directory contains no
  `*.tmp`. A partially written file is one the next session reads as complete.
- **Refuse an operator id that is not one path component.** Run
  `control_operator.py operator --run <run> --label handoff-bad-operator -- handoff --instance ../escape --status nope`.
  Exit `2`, stderr is `the operator id '../escape' is not usable`, and nothing
  named `escape.md` exists anywhere under `projects/`.
- **Refuse a status that is only whitespace.** Run the same command with
  `--status "   "`. Exit `2` and stderr opens `Usage: operator handoff`. It
  passes a truthiness check and would otherwise write an empty `## Status`.
- **Refuse a mistyped switch rather than ignoring it.** Run with `--norestart`,
  one hyphen short of the real flag. Exit `2`, stderr is
  `operator handoff: unknown option --norestart`, and **no** restart marker
  appears. This is the case worth driving: skipping the unknown flag silently
  would end the session that the flag was typed to preserve.
- **Proof.** `artifacts/transcript.md` holds each command with its exit code and
  both streams; `artifacts/after-handoff/` and `artifacts/after-restart/` hold
  the home on either side of the restart request.

## Gotchas

- **The operator id is the record id, not the display name.** The record id
  never changes when the operator is renamed, and `supervisor.py` probes
  `handoff_state(workdir, instance.id)`. Driving with the display name files
  the handoff where the next session will not look, while the restart happens
  anyway. The preamble advertises the id for this reason; pass what it printed.
- **The working directory decides the project**, exactly as it does for
  `operator handoff`. The helper runs from the registered checkout unless `--cwd`
  says otherwise.
- **`--no-restart` is a switch and takes no value.** It is deliberately absent
  from the value-flag list, so writing `--no-restart yes` makes `yes` a stray
  positional and the command exits `2`.
- **A value that starts with a dash needs the joined form.** `--status
  "- shipped the parser"` is refused, because taking any following token
  blindly is what let `--context --no-restart` swallow the switch and restart a
  session that asked not to be restarted. Pass it as one argument beginning
  `--status=`, quoted for your shell. The refusal describes that shape rather
  than printing a line to paste: no quoting is correct for every shell once the
  value carries quotes of its own, and a suggestion that drops them silently is
  worse than none.
- **Exit `2` and exit `1` mean different things here.** `2` is the command
  refusing what it was asked (usage, unusable operator, unknown option) and nothing
  was written. `1` is the command trying and failing (unregistered project,
  unreadable catalog, a write that failed), which is a state question.
- **A restart that could not be requested still leaves the handoff.** The file
  is the durable half, so the command reports the failure and exits `1` rather
  than pretending the session is ending. Check for the marker, not the exit code
  alone.
- **The reader deletes the handoff, and nothing enforces it.** A file left on
  disk is either one nobody picked up or one a session read and died before
  removing, and the supervisor cannot tell those apart. Start from a fresh `up`
  rather than reasoning about a handoff a previous round left behind.
