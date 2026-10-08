# Session handoff

An operator ends its own session and leaves the next one a record. `operator handoff` writes a markdown file, then sets the restart marker the supervisor polls. The supervisor tears the session down and launches the next, whose preamble names the file. The file comes first, because the supervisor acts on the marker as soon as it sees it. `--no-restart` writes the file and leaves the session running.

## Sub-features

- `handoff-write` stores status, next and context at `projects/<guid>/handoff/<id>.md`. A section appears only when its flag was given.
- `handoff-restart` sets `restart/<id>`. The supervisor claims it and launches session 2, whose preamble names the file.
- `handoff-checkpoint` `--no-restart` writes the file and leaves the session running.
- `handoff-operator-id` the file and the marker use the operator id, taken from the caller's process ancestry.
- `handoff-forged` `--instance` naming another operator exits 2 and writes nothing.
- `handoff-outsider` a caller outside every operator session exits 2 and writes nothing. A person at a shell is one, and so is this harness.
- `handoff-usage` a missing or whitespace-only status exits 2 with the usage line.
- `handoff-unknown-option` a mistyped flag exits 2 instead of being ignored.
- `handoff-dash-value` a value that opens with a dash is refused unless joined, as in `--status=- shipped`.
- `handoff-replace` a second write replaces the whole file and leaves no `.tmp` beside it.
- `handoff-unregistered` an operator whose repo is not a project exits 1. Tests only.

## How to get to it (user POV)

- Only an agent reaches it. It runs `operator handoff --status "..."` from inside its own session, with optional `--next`, `--context` and `--no-restart`.
- Every preamble advertises `operator handoff --status "..." --next "..."`.
- The menu has no handoff. A person at a shell is refused, because no operator session is among the shell's ancestors.

## Driving it with control_operator

Preconditions:

- `up` has run and `doctor --run <run>` printed `doctor: healthy`.
- Scripts are written before `start`. Any operator whose `NAME.json` hands off also has a `NAME.s2.json`.

- **Script a checkpoint and a real handoff.**
  - `agent --run <run> chk '{"op":["handoff","--status","checkpoint only","--no-restart"]}'`
  - `agent --run <run> lead '{"op":["handoff","--instance","chk","--status","forged"]}' '{"op":["handoff","--status","drove it","--next","read it back","--context","from the fake"]}'`
  - `agent --run <run> --session 2 lead '{"op":["list"]}'`
- **Checkpoint.** `operator --run <run> -- start chk "role=chk"`, then `wait --run <run> --file artifacts/agents/chk/commands.log --contains "exit " --timeout 60`.
  - chk's `commands.log`: exit 0 and `handoff written to <home>\projects\<guid>\handoff\<chk id>.md`, with no restart line.
  - The file reads `# Handoff: <chk id>`, then `## Status` and `checkpoint only`.
  - chk's `starts.log` still holds one line 15 seconds later.
- **Handoff.** `operator --run <run> -- start lead "role=lead"`, then `wait --run <run> --file artifacts/agents/lead/starts.log --contains "session=2" --timeout 90`. Found after about 17 seconds.
  - lead's `commands.log`: the forged call exits 2 with `operator handoff: this session is lead, not chk`. The real one exits 0 with `handoff written to …\<lead id>.md` and `restart requested for <lead id>`.
  - lead's `starts.log` holds `session=1` and `session=2` under the same `id=` and two different `session_id=`.
  - `prompt-2.txt` says `A handoff from the previous session is waiting for you. Read it before doing anything else. It is at <path>`.
  - The file holds `## Status` `drove it`, `## Next` `read it back` and `## Context` `from the fake`.
  - `evidence --run <run> --label after-lead` lists both `.md` files and no `restart/<lead id>`. The supervisor removed the marker when it relaunched.
- **Refusals from the harness.** These need no session.
  - `operator --run <run> -- handoff --status "x" --no-restart`: exit 2, `operator handoff: this process is not inside an operator session`.
  - `--status "   "`: exit 2, `Usage: operator handoff --status TEXT [--next TEXT] [--context TEXT] [--instance NAME] [--no-restart]`.
  - `--norestart`: exit 2, `operator handoff: unknown option --norestart`.
  - `--status "- shipped"`: exit 2, `operator handoff --status needs a value, and '- shipped' looks like an option. To mean it literally, pass it as one argument starting --status=, quoted for your shell`.
  - `evidence` after these shows no handoff file and no marker.
- **Refusals from inside a session.** Script them as `op` steps and read `commands.log`.
  - `--status x --no-restart yes`: exit 2, `operator handoff: this session is gx, not yes`.
  - `--status "   " --no-restart`: exit 2 with the usage line.
  - `--status=- shipped the parser --no-restart`: exit 0, and `## Status` reads `- shipped the parser`.
- **Replace.** Script two `--no-restart` handoffs, the first with `--status first --next "old next"`, the second with `--status second`. Both exit 0 and name the same file. It holds only `## Status` `second`, and its directory holds no `.tmp`.

## Gotchas

- **A handoff script needs a session 2 script.** The fake reads `NAME.sN.json`, else `NAME.json`. Without `lead.s2.json`, session 2 runs `lead.json` again, hands off again, and relaunches forever.
- **Identity is checked before `--instance` is resolved.** From the harness, a forged or unknown `--instance` prints `not inside an operator session`, not the cross-check line. Drive the cross-check from an `op` step.
- **A bare word is the operator name.** `operator handoff alpha --status x` cross-checks `alpha`, so `--no-restart yes` makes `yes` the name. Inside a session that exits 2 and writes nothing.
- **Exit 2 means refused, exit 1 means failed.** 2 is usage, an outsider, a forged name or an unknown option, and nothing was written. 1 is a write that failed, a repo that is not a project, or a restart that could not be requested after the file landed. Check for the file, not the exit code alone.
- **The operator's repo decides the project**, not the caller's working directory. A `cd` elsewhere still writes into the recorded repo's project.
- **The reader deletes the file, and nothing enforces it.** The fake never deletes one, so a handoff file stays in `projects/<guid>/handoff/` for the rest of the run. Start a fresh `up` rather than reason about one a previous round left behind.
- **The marker is gone by the time you look.** The supervisor claims `restart/<id>` within one poll. Prove the restart with `session=2` in `starts.log`, not with the marker.
