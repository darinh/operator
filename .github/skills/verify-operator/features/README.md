# operator verification map

The map of what a person and an agent can do with `operator`, and how to drive each feature against a disposable run. Read this index first. Then use the matching feature file as the recipe.

## Baseline preconditions

- Make a run with `control_operator.py up --run-id <id>`. Every later verb takes `--run .verify-operator\<id>`.
- Drive only after `doctor --run <run>` prints `doctor: healthy` and exits 0.
- Never drive `~/.operator`, or a run this session did not create.
- End every run with `down --run <run>`, including a failed one. It must print `down: clean`.

## Driving conventions

- **A person's command** goes through `operator --run <run> -- <verb> ...`, in the scratch project with stdin closed.
- **An agent's command** is a step in the fake's script, written with `agent` before that operator starts. Only commands run there are agent callers. The harness itself is always a person.
- **Agent reactions come from handlers.** An agent that should act on a message gets an `{"on": TEXT, "do": [...]}` step. A person's `send` to it is the trigger.
- **Wait on files, never on sleeps.** Use `wait --file artifacts/agents/NAME/<log> --contains TEXT`. Pick TEXT that appears only in the thing you are waiting for, not in a label.
- **Paths are shown as Windows prints them.** Recipes were measured on Windows, so screens and outputs show `<run>\repo`. On Linux or macOS expect `/`. `--file` patterns use `/` everywhere.
- Treat every command in a recipe as literal. Keep quoted text and flags unchanged.

## Proof rules

- Drive the installed console script, never the Python function behind it.
- Proof is a command, its exit code, and the state it left. That state lives in `artifacts/transcript.md`, `artifacts/agents/`, `evidence` snapshots and `screen` captures.
- Snapshot with `evidence --label` on both sides of a change. The difference between the two is the proof.
- Report a path you could not reach with the command you tried and the precondition that was missing. Never report it as verified through a different path.

## Feature entry contract

Each feature file starts with an H1 and one paragraph of user-visible behaviour. Then come exactly four H2 sections, in this order:

1. `Sub-features`. Short IDs, one line each.
2. `How to get to it (user POV)`. Every way a person or an agent reaches it.
3. `Driving it with control_operator`. Starts with `Preconditions:`, then labelled bullets. Each bullet pairs an action with an exact command and the result to expect.
4. `Gotchas`. Traps that waste or invalidate a run.

Name user paths, commands and observable proof. Leave implementation detail to the code.

## Harness commands

| Verb | Use |
| --- | --- |
| `up` | Make a run: venv, fake copilot, home, scratch project |
| `doctor` | Read-only health check |
| `operator` | Run any `operator` verb as a person |
| `agent` | Script what an operator's agent runs |
| `menu` | Open the menu TUI in a session the run owns |
| `keys` | Press keys in the menu or an operator's pane |
| `screen` | Save a pane as text |
| `wait` | Wait for a file, text in a file, or text on a screen |
| `evidence` | Snapshot the home |
| `down` | Stop everything, remove the instance, keep the artifacts |
| `exec` | Hidden. Run `operator` in this console with the run's environment. `menu` runs it in the pane. |

[SKILL.md](../SKILL.md) gives each verb's flags and the script step format.

## Core features

- [Lifecycle](./lifecycle.md). Start, list, stop, restart, rename and delete as a person.
- [Child operators](./child-operators.md). An agent starts children, and the rules on what it may touch: caps, `--dir`, cascade stop, re-homing on delete.
- [Mail](./mail.md). `send` and `inbox` between a parent and its child, with a person as the parent of each top-level operator.
- [Menu](./menu.md). The keyboard TUI: start and attach, the operator tree, row actions.
- [Session handoff](./session-handoff.md). An agent ends its session, and the next one is told what it left.

## Coverage gaps

- **Real Copilot.** Every recipe uses the fake. What a model does with a preamble, a typed message or a handoff file is out of reach here. One manual run with the real `copilot` is the check for that.
- **Nested attach.** The harness strips the multiplexer's pane variables, so it cannot see #49: attaching from inside a tmux or psmux pane fails while `operator` exits 0.
- **Crash recovery.** Nothing here kills a supervisor mid-session, so `operator recover` and the `delivering` requeue are not driven. Only the refusal of `recover` to an agent is.
- **Linux.** Runs were proven on Windows with psmux. Each Windows-specific step in the harness has a POSIX branch, but no Linux tmux run has been recorded.
- **The product's `operator doctor`.** The harness's `doctor` checks the run. No recipe drives the product's own `operator doctor`.
- **Start options.** `start --fresh`, `--agent` and other flags `start` hands to Copilot are not driven. The fake reads only the prompt, so it cannot show which flags reached it.
