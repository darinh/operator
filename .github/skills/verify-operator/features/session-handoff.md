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
- `handoff-operator-id` files under the operator **id**, which is what the reader probes with. A display name resolves to that id first.
- `handoff-unknown` a caller that is not inside exactly one operator session exits 2 and writes nothing. So does `--instance` naming a different operator.
- `handoff-unregistered` refuses when the derived operator's repo is not a project.
- `handoff-usage` refuses a missing or whitespace-only status.
- `handoff-unknown-option` refuses a mistyped flag instead of ignoring it.

## How to get to it (user POV)

- From inside the session, run `operator handoff --status "<text>"`, with
  optional `--next`, `--context` and `--no-restart`. The command identifies the
  caller by process ancestry. It does not take the operator as an address.
- `--instance <name-or-id>` is optional. If you pass it and it is not this
  session, the command exits 2 and writes nothing.
- Handoff is not on the keyboard menu. The agent runs the verb from inside the
  session. A person at the keyboard, and this control harness, are not that
  process. Both are refused.
- Every launch preamble advertises `operator handoff --status "..." --next "..."`.

## Driving it with control_operator

Preconditions:

- `up` has run and `doctor --run <run>` reports `doctor: healthy`.
- An operator record named `verify-handoff` exists in this project. `operator start --name verify-handoff` creates one. `up` alone does not.
- No handoff exists yet for that operator.

- **This harness cannot complete a handoff.** It is not a descendant of the recorded copilot, so `operator handoff` exits 2 with `this process is not inside an operator session` and writes nothing. That refusal is the check. The writes, the marker claim, and the forged `--instance` are covered by `tests/test_handoff_custody.py` and `tests/test_restart_claim.py`.
- **A recorded drive still shows the refusal.** Run
  `control_operator.py operator --run <run> --label handoff-write -- handoff --status "drove the front door" --next "read it back" --no-restart`.
  Exit `2`. Stderr contains `not inside an operator session`. No handoff file and no restart marker.
- **Prove nothing was written.** Run
  `control_operator.py evidence --run <run> --label after-handoff`. The manifest
  lists neither `projects/<guid>/handoff/<id>.md` nor `restart/<id>`.
- **Parse still refuses before it asks who you are.** These do not need a
  session, so the harness can drive them. A status of only whitespace exits 2
  and stderr opens `Usage: operator handoff`. `--norestart` exits 2 with
  `operator handoff: unknown option --norestart` and writes nothing. A value
  that starts with a dash is refused the same way, before any file is written.
- **Unknown names and path-shaped arguments are not what this harness sees.**
  Identity is checked before `--instance` is resolved, so both of those
  commands exit 2 with `not inside an operator session`. The "no such operator"
  and "this session is X, not Y" lines are covered by
  `tests/test_handoff_custody.py`.
- **Proof.** `artifacts/transcript.md` holds each command with its exit code and
  both streams. `artifacts/after-handoff/` holds a home with no handoff file
  and no restart marker.

## Gotchas

- **The file and the marker use the record id.** The caller does not choose it. Ancestry does. `supervisor.py` probes `handoff_state(workdir, instance.id)` and accepts the marker only when its `id` and `session` match the session it is watching. A forged `--instance` exits 2 and writes nothing.
- **The operator's repo decides the project**, not the caller's working directory. A `cd` elsewhere still writes into the repo recorded for that operator.
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
  refusing what it was asked (usage, not inside a session, a forged
  `--instance`, an unknown option) and nothing was written. `1` is the command
  trying and failing (the derived operator's repo is not a project, an
  unreadable catalog, a write that failed), which is a state question.
- **A restart that could not be requested still leaves the handoff.** The file
  is the durable half, so the command reports the failure and exits `1` rather
  than pretending the session is ending. Check for the marker, not the exit code
  alone.
- **The reader deletes the handoff, and nothing enforces it.** A file left on
  disk is either one nobody picked up or one a session read and died before
  removing, and the supervisor cannot tell those apart. Start from a fresh `up`
  rather than reasoning about a handoff a previous round left behind.
