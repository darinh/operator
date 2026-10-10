---
name: verify-operator
description: Drive operator the way its users do, against a disposable instance, and keep the proof. Starts real operators with a scripted fake copilot, drives the menu TUI by keystroke, and reads what landed on disk. Use when a change touches start, stop, list, the menu, child operators, mail, handoff or the supervisor, and you need evidence the real commands behave, not just a green pytest.
---

# Verify operator

`operator` starts supervised Copilot CLI sessions in a terminal multiplexer, records them, and lets them start children, mail each other and hand off. This skill runs the real thing: the checkout's `operator` console script, real supervisors and runners in real psmux sessions, and the menu TUI driven by keystroke. The one stand-in is Copilot itself. A fake `copilot` runs a script of `operator` commands from inside the session, the way an agent would, and logs everything typed into it.

`python -m pytest -q` is not a substitute. The suite swaps out the multiplexer, the home and the catalog. This skill swaps out only Copilot.

Read [features/README.md](./features/README.md) before driving. It is the map, and each feature file is a recipe.

## Isolation

A run never touches `~/.operator`, the real Copilot, or a global `operator`.

- Every child process gets `COPILOT_OPERATOR_HOME` set to the run's home before it starts. The kernel reads it at import, so setting it later would address the real home. `doctor` checks the home is not `~/.operator`.
- The run has its own venv holding the checkout under test and the fake `copilot`. The venv is first on `PATH`, so the supervisor's `shutil.which("copilot")` finds the fake.
- Children never see the multiplexer pane variables (`TMUX`, `TMUX_PANE`, `PSMUX_SESSION`, `PSMUX_TARGET_SESSION`). `operator` therefore behaves as it would in a person's plain terminal, even when the harness runs inside a pane.
- `down` fails the run if any runner log shows a launch of anything but the fake.

## Launch

```
python .github/skills/verify-operator/control_operator.py up --run-id <id>
```

About 15 seconds. It needs the network once, for pip. It prints the run directory. Every other verb takes it as `--run <run>`. The relative form `.verify-operator\<id>` works from the checkout root.

```
.verify-operator/<id>/
    run.json    paths the other verbs read          (kept by down)
    home/       COPILOT_OPERATOR_HOME                (removed)
    venv/       the checkout under test + fake copilot (removed)
    fake/       the fake's package                   (removed)
    repo/       a scratch git repo, the registered project (removed)
    logs/       COPILOT_LOG_DIR                       (removed)
    artifacts/  transcript, screens, snapshots, agent logs (kept)
```

The checkout under test is the one this file sits in, found with `git rev-parse --show-toplevel`. From a worktree it is that worktree. `--checkout` overrides it. Operators run in `repo/`, a scratch git repo registered in the run's catalog, never in the checkout.

`.verify-operator/` is gitignored.

## Doctor

```
python .github/skills/verify-operator/control_operator.py doctor --run <run>
```

Read-only. Drive only after it prints `doctor: healthy` and exits 0. It checks that `operator` and `copilot` both resolve to the run's venv, that `copilot` is the fake, that the venv imports this checkout's `operator_kernel`, that the home is isolated and registers `repo/`, and that `tmux` or `psmux` is on `PATH`. `--no-mux` skips the last check, for CI.

## Drive

Every verb takes `--run <run>`.

| Intent | Command |
| --- | --- |
| Run any `operator` verb as a person would | `operator --run <run> [--label L] [--cwd DIR] -- <verb> <args>` |
| Script what an operator's agent does | `agent --run <run> [--session N] NAME STEP...` |
| Open the menu TUI | `menu --run <run>` |
| Press keys in the menu or an operator's pane | `keys --run <run> [--target menu\|NAME] [--text T] KEY...` |
| Save a pane | `screen --run <run> [--target menu\|NAME] --label L` |
| Wait for something | `wait --run <run> (--file GLOB \| --screen menu\|NAME --contains T) [--contains T] [--timeout S]` |
| Snapshot the home | `evidence --run <run> --label L` |

`operator` runs the venv's console script in `repo/` with an empty stdin pipe. A verb that would ask a question therefore takes its no-terminal path, as it would in a script. Not `DEVNULL`: on Windows that reads as a terminal. Everything after `--` reaches `operator` untouched. Each call lands in `artifacts/transcript.md` with its exit code and both streams.

`wait --file` takes a glob relative to the run, like `artifacts/agents/lead/stdin.log`, and refuses one that could leave it: absolute, rooted, on a drive, or holding `..`. A match through a link that points outside the run does not count. `agent NAME`, `screen --label`, `evidence --label` and `up --run-id` each become one file or directory name, so each must be a plain name: letters, digits, `.`, `_` and `-`, starting with a letter or digit. The fake files an operator whose name is not plain, like `scout two`, under its id instead, in `artifacts/agents/<id>/`. The id exists only once `start` makes the operator, so its first session runs with no script. `wait --file "artifacts/agents/op-*/starts.log"` finds that directory, whose name is the id, and `agent --session 2 <id> STEP...` scripts the next session. `wait --screen NAME` keeps polling until NAME's record exists, so it can be started before the operator is.

### Scripting an agent

Write the script before the operator starts. The fake reads the operator's name and id from its preamble, `You are operator NAME (ID).`. Below, NAME is that name, or the id when the name is not plain. The fake reads `artifacts/scripts/NAME.sN.json` for session N, or else `NAME.json`. N counts the fake's launches under NAME. A restart after a stop is the next session, as is the launch after a handoff, and a rename starts the count again at 1. A `NAME.json` that hands off needs a `NAME.s2.json`, or every session runs it again and hands off forever. Each step is one JSON object.

| Step | Effect |
| --- | --- |
| `{"op": ["start", "scout", "role=scout"]}` | Run `operator` with these arguments, as a descendant of the session. That makes it an agent caller. Its stdin is NUL, which is what Copilot's shell gives a command on Windows: `isatty()` says True and a read is empty. |
| `{"sleep": 2}` | Wait. |
| `{"cd": "sub"}` | Change directory. |
| `{"on": "ping", "do": [STEP...]}` | From now on, run these steps whenever a typed line contains `ping`. |
| `{"exit": 0}` | End the session. |

After its steps, the fake reads stdin until it is killed or a line is `/exit`, which is what stop and handoff type. It writes everything under `artifacts/agents/NAME/`.

| File | Holds |
| --- | --- |
| `starts.log` | One line per launch: session number, pid, operator id, session id, cwd, which `operator` it found, which script it ran |
| `prompt-N.txt` | The whole preamble session N received |
| `commands.log` | Each `operator` command with its start time, cwd, exit code, duration and output |
| `stdin.log` | Each line typed into the session, which is how mail arrives |

`agent` refuses a step the fake cannot run and writes nothing.

### Example

A parent starts a child. They trade mail, the parent stops the child and hands off, and its second session mails the person. PowerShell 7, from the checkout root:

```powershell
$C = ".github\skills\verify-operator\control_operator.py"
$R = ".verify-operator\demo"
python $C up --run-id demo
python $C doctor --run $R
python $C agent --run $R lead '{"op":["start","scout","role=scout"]}' '{"on":"hello","do":[{"op":["send","scout","ping"]}]}' '{"on":"] pong","do":[{"op":["stop","scout"]},{"op":["handoff","--status","done"]}]}'
python $C agent --run $R --session 2 lead '{"op":["send","human","finished"]}'
python $C agent --run $R scout '{"op":["send","lead","hello"]}' '{"on":"ping","do":[{"op":["send","lead","pong"]}]}'
python $C operator --run $R -- start lead "role=lead"
python $C wait --run $R --file artifacts/agents/lead/commands.log --contains "sent to human" --timeout 120
python $C operator --run $R -- list
python $C operator --run $R -- inbox
python $C down --run $R
```

In bash, set `C=.github/skills/verify-operator/control_operator.py` and `R=.verify-operator/demo`, and the remaining lines work unchanged.

## Evidence

- `artifacts/transcript.md` holds every `operator` call, key press, screen save and teardown note.
- `evidence --label L` copies `operator.log`, the catalog, every operator record, every handoff file, everything under `restart/` and every mail file into `artifacts/L/`, with a `MANIFEST.txt`. Take one on both sides of a change. The difference is the proof.
- `screen --label L` saves a pane to `artifacts/screens/L.txt`. The surface is a terminal, so a text capture is the faithful record.
- `artifacts/agents/` holds what each fake agent ran and received.

Proof is a command, its exit code and the on-disk state it left. Stdout alone is not proof.

## Cleanup

```
python .github/skills/verify-operator/control_operator.py down --run <run>
```

Run it after every run, and after every failed one. It stops every recorded operator, kills the run's psmux sessions and the menu's, takes an `at-down` snapshot, and removes everything but `artifacts/` and `run.json`. It is idempotent, and it refuses a directory with no `run.json`, which only `up` writes. Every verb that drives the run then refuses with `is down`, while `wait --file`, `evidence` and `doctor` still read what is left. Start the next with a new `--run-id`.

It prints `down: clean` and exits 0, or prints `FAILED` and exits 1 when any of these happened:

- A process's command line still named a path in the run, outside `artifacts/`, 20 seconds after the stops. It kills the process, because a leftover means operator leaked one. That includes anything a person started there, such as an editor on a file in `repo/`, so close those first. An editor on `artifacts/` is spared.
- A runner launched anything but the fake.
- A directory could not be removed.

`artifacts/down.txt` keeps every report, each ending with its verdict, so a second `down` cannot hide a failed first one.

## Gotchas

- **Mail takes about 10 seconds a hop.** A supervisor types waiting mail on its poll, and only once its session has a session id. Use `wait --file artifacts/agents/NAME/stdin.log --contains TEXT --timeout 30`, never a fixed sleep.
- **Stopping waits for the session to end.** `operator stop` returns once the supervisor has gone. With the fake that is a few seconds.
- **A pane does not inherit `PATH`.** psmux gives it the registry `PATH`. The fake puts the venv back in front, and `menu` runs the TUI through the hidden `exec` verb for the same reason. Never start the TUI in a pane with a bare `operator`.
- **The menu's pane outlives the menu.** When `operator` quits or finishes an attach, the pane prints `[operator exited N]` and stays until `down`, so the last screen can still be saved. `menu` again closes it and opens a fresh one.
- **Attach from the menu nests inside its pane.** Start an operator, Attach and Start and attach all show the operator's session in the menu's pane. `keys C-b d` detaches. In a real tmux or psmux pane, psmux refuses that nesting and `operator` still exits 0. That is #49, and this harness cannot see it, because it strips the pane variables.
- **psmux pre-spawns a warm server** that holds its start directory open. The harness sets `PSMUX_NO_WARM=1` everywhere. A psmux started by hand without it can keep a run directory from being removed.
- **Handoff works only from inside a session.** It walks process ancestry to the recorded copilot, so `operator --run R -- handoff ...` is refused. Script it as an `op` step instead.
- **One run, one id.** `up` refuses a run directory that already has a `run.json`. Pick a new `--run-id`, or `down` and delete the old one.

## Helpers

| Verb | Purpose |
| --- | --- |
| `up` | build the venv, scratch project, home and fake |
| `doctor` | read-only health check |
| `operator` | run the front door in the scratch project |
| `agent` | write a fake agent's script |
| `menu` | open the menu TUI in a session the run owns |
| `keys`, `screen`, `wait` | drive and read panes, and wait for files |
| `evidence` | snapshot the home under a label |
| `down` | stop everything, remove the instance, keep the artifacts |
| `exec` | hidden. Runs `operator` in this console with the run's environment. `menu` runs it with `--hold`. |

The harness and the fake have their own tests, because a harness bug produces a false proof rather than a failed one. They check the fake against the kernel's own runner and preamble.

```
python -m pytest .github/skills/verify-operator -q
```
