# Lifecycle

A person starts an operator in the directory they stand in, sees it in `operator list`, stops it, starts it again, renames it and deletes it. A stopped operator keeps its record and comes back as its next session. Delete removes the record, its settings and its mailbox, and refuses while the operator runs.

## Sub-features

- `start-bare` `operator start` with no name starts this directory's operator, or makes one named after the directory.
- `start-named` `operator start NAME "task"` makes NAME and launches its first session.
- `start-running` starting a running operator says so and exits 1.
- `list` running and offline operators, each with its directory, and a pid when running.
- `list-empty` says how to start one.
- `stop` stops the session and its supervisor. Stopping a stopped operator says the same and exits 0.
- `restart` starting a stopped operator launches its next session.
- `rename` works running or stopped, and refuses a name that is taken.
- `delete` needs a stopped operator, and `--yes` when there is no terminal.

## How to get to it (user POV)

- `operator start [NAME] [TASK]`, `operator list`, `operator stop NAME`, `operator rename NAME NEW` and `operator delete NAME [--yes]` at a shell.
- The menu reaches the same verbs. Start an operator, then List operators and pick a row: Stop, Start, Start and attach, Rename, Delete. See [menu.md](./menu.md).
- An agent reaches start, stop and delete only for its own children. See [child-operators.md](./child-operators.md).

## Driving it with control_operator

Preconditions:

- `up` has run and `doctor --run <run>` printed `doctor: healthy`.
- `<run>` is `.verify-operator\<id>`. `$C` is `.github\skills\verify-operator\control_operator.py`.

- **Empty list.** `python $C operator --run <run> -- list`. Exit 0, `No operators yet. Start one with: operator start`.
- **Bare start.** `python $C operator --run <run> -- start`. Exit 0, `started repo (pid N)`, because the scratch project's directory is `repo`.
- **Named start.** `python $C operator --run <run> -- start alpha "role=alpha"`. Exit 0, `started alpha (pid N)`. Then `python $C wait --run <run> --file artifacts/agents/alpha/starts.log --contains session=1`. The session is up within about a second.
- **List.** `python $C operator --run <run> -- list`. `Running:` shows `alpha  (<run>\repo)  pid N`, and `Offline:` shows `(none)`.
- **Start a running one.** `python $C operator --run <run> -- start alpha`. Exit 1, `alpha is already running`. Adding a task gives the same.
- **Delete a running one.** `python $C operator --run <run> -- delete alpha --yes`. Exit 1, `stop it first: operator stop alpha`.
- **Rename onto a taken name.** `python $C operator --run <run> -- rename alpha repo`. Exit 2, `an operator named 'repo' already exists`.
- **Rename.** `python $C operator --run <run> -- rename alpha beta`. Exit 0, `renamed alpha to beta`, and `list` shows `beta` with the same pid.
- **Stop.** `python $C operator --run <run> -- stop beta`. Exit 0, `stop requested for beta`, in about 2 seconds. A second stop prints the same and exits 0. `list` shows `beta` under `Offline:` with no pid.
- **Delete without `--yes`.** `python $C operator --run <run> -- delete beta`. Exit 2, `pass --yes to delete without a terminal`.
- **Restart.** `python $C operator --run <run> -- start beta`. Exit 0. The fake logs the next session in `artifacts/agents/beta/starts.log`.
- **Delete.** Stop it, then `python $C operator --run <run> -- delete beta --yes`. Exit 0, `deleted beta`. `list` no longer shows it, and a second delete exits 1 with `No operator 'beta'.` Take `evidence --label` before and after. The snapshot flattens paths with `__`, and `operators__<id>.json`, `restart__<id>.*` and `mail__<id>__*` are gone from the second one.

## Gotchas

- **The fake's session count is per name.** After `rename alpha beta`, beta's next launch logs `session=1` under `artifacts/agents/beta/`, although the kernel sees a second session. Script it as `beta.json`, or `beta.s1.json`.
- **Delete's question needs a terminal.** The harness has none, so only the `pass --yes` path is reachable from `operator`. The `Delete? [y/N]` question is reached through the menu.
- **Attach needs a terminal too.** `operator attach NAME` through the harness has no terminal to hand over. Attach through the menu, or read an operator's pane with `screen --target NAME`.
- **Rename keeps the id.** The record id and every file keyed by it stay the same. Only the name in the record and in `list` changes.
