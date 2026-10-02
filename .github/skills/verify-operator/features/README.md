# operator verification map

This directory is the maintained source for verifying the user-facing behaviour of
`operator`. Read this index before driving, then use the matching feature file as
the recipe.

## Baseline preconditions

- Create a disposable instance with `python .github/skills/verify-operator/control_operator.py up`.
- Keep the printed `run` path. Every later command takes it as `--run <run>`.
- Run `doctor --run <run>` and require `doctor: healthy` with exit `0`.
- Never drive the real `~/.operator`. `doctor` asserts the home is elsewhere; if
  that check ever fails, stop rather than continue.
- Never drive an instance this run did not create with `up`.

## Driving conventions

- Start every recipe from a fresh `up` unless its preconditions say otherwise.
- Pass console-script flags after `--`; they reach the command untouched.
- Treat every command as literal. Keep quoted text and flags unchanged.
- Front-door actions go through `control_operator.py operator --run <run> -- <args>`.
- Give every call a `--label`, because the label names its transcript entry.
- Do not remove proof artifacts during cleanup. `down` already leaves them.

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

1. `Sub-features`. Short IDs, one line each.
2. `How to get to it (user POV)`. Every user entry point.
3. `Driving it with control_operator`. Starts with `Preconditions:`, then
   labelled bullets pairing a user action with an exact command and an observable
   result.
4. `Gotchas`. Traps that waste or invalidate a run.

Keep implementation detail out of the map. Name user paths, required state,
commands and observable proof.

## Harness commands

`control_operator.py` has five subcommands and no others.

- `up` creates an isolated home and registers the repo.
- `doctor` is a read-only health check of one run.
- `operator` runs the `operator` front door. `--cwd` overrides the working directory.
- `evidence` snapshots home state into artifacts.
- `down` removes the instance and keeps the artifacts.

## Core features

- [Session handoff](./session-handoff.md). `operator handoff`, the file the next
  launch announces, the restart marker, and the operator-id key both share.

## Coverage gaps

None outstanding. The feature this map names is driven against real code.

The one thing this skill still does not drive is the supervisor loop itself
(`run_loop_mode` and its relaunch behaviour). That needs a multiplexer and a
stub agent, which is a harness of a different kind rather than a missing recipe.
