# operator

Start a supervised Copilot CLI session, and let it hand off to the next one.

You give an agent a repository and walk away. `operator` starts a Copilot CLI session in a terminal multiplexer and a supervisor that relaunches it. The agent ends a session with `operator handoff`. The next session is told where that handoff is. The tool writes no code itself.

The unit it supervises is an **operator**. An operator outlives the sessions it runs.

## Install

```
pip install -e .
operator doctor
```

`doctor` names anything missing and how to install it. It needs the GitHub Copilot CLI on PATH and a terminal multiplexer (`tmux`, or `psmux` on Windows).

Python 3.10 or newer. Nothing else is required.

## Quickstart

```
cd ~/repos/yourproject
operator start --name alpha
operator list
operator attach alpha
```

`start` registers the current directory. The agent inside the session hands off with:

```
operator handoff --instance alpha --status "what you did" --next "what is next" --context "what the next session needs"
```

That writes the handoff file and asks the supervisor to start the next session. `alpha` is the operator's name or its id. The file and the restart marker use the id.

## Use it

```
operator                      the menu
operator doctor               is this machine ready
operator start --name alpha   start a supervised operator here
operator list                 list operators, running and not
operator attach alpha         attach your terminal to it
operator stop alpha           ask its supervisor to stop
operator rename alpha bravo   rename an operator
operator delete alpha --yes   delete an operator and its settings
operator recover              list operators a crash or reboot took down
operator recover --all        bring those supervisors back
operator handoff --instance alpha --status "what you did"
                              end this session and leave the next one the file
```

## What it actually does

**Relaunches.** The supervisor starts the session, watches it, and starts the next one when the agent hands off or the session ends. A run stops after 1000 sessions.

**Stops a crash loop.** Five unexpected exits in a row, each inside 120 seconds, ends the run. A session that stayed up longer resets that count.

**Survives a reboot.** `operator recover` lists operators whose supervisor is gone. `operator recover --all`, or an operator name, starts the supervisor again and continues the session numbering.

## What it does not do

It does not decide when an operator should hand off. That is the repository's business. The launch preamble only says that a handoff is possible and how to run it.

It does not give an operator its next task. The next session reads the handoff the previous one wrote.

Agents run as you. There is no separate account and no trust boundary.

## Layout

| | |
| --- | --- |
| `operator_kernel/` | the supervisor, the handoff, the launch preamble |
| `operator_cli/` | the one entry point, including the menu |

## Contributing

`python -m pytest -q` runs the suite. CI is Windows and Linux on Python 3.10 and 3.12.

Before opening a PR, read `.github/skills/pr-gate/SKILL.md` and run `python .github/skills/pr-gate/preflight.py --pr <number>`.
