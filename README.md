# operator

`operator` starts a supervised Copilot CLI session that relaunches, and lets the agent hand off to a fresh session.

The unit it supervises is an operator. An operator outlives the sessions it runs. The tool writes no code itself.

## Install

Python 3.10 or newer. The GitHub Copilot CLI must be on PATH, and so must `tmux` or `psmux`.

```
pip install -e .
operator doctor
```

`doctor` names anything missing and how to install it.

## Menu

`operator` with no arguments opens a menu when stdin and stdout are a terminal. Otherwise it prints help and exits 2.

Up and Down move the highlight. Space toggles a row on the recover screen. Enter confirms. Esc goes back. Esc on the main menu quits. Ctrl-C exits 130 and restores the terminal.

The main menu is Start an operator, List operators, Recover operator sessions when any need it, and Quit. When none need recovery, that row says so and selecting it stays on the menu.

Start an operator asks for a name, prefilled with the operator already working in this directory or else the directory's name. The name is empty when several operators work here. Enter starts that operator and attaches this terminal to it, or attaches if it is already running. Attach, and Start and attach, on the list screen also leave the menu and take this terminal. Every other action returns you to the screen you came from.

## Menu and command line

Each menu choice that runs a command calls the same handler as the typed command, and `tests/test_surfaces.py` checks that they are the same functions. List operators splits operators with the function `operator list` uses. The same test file runs each choice both ways in a sandbox. Both ways must spawn, stop, recover and attach the same operators, leave the same operators and projects on disk, and tell the user the same thing. The test also fails when a command, an option or a menu item has no case, when the tables below disagree with the cases, and when a row of a one-sided table gives no reason. It finds menu items by walking the menu in four fake states. A choice none of them shows can still escape when an index computed from data picks it from labels the menu draws elsewhere, and so can a new key in the menu's input loops.

| Menu | Command line |
| --- | --- |
| Start an operator > Enter | `operator start --attach` |
| Start an operator > type NAME > Enter | `operator start NAME --attach` |
| List operators | `operator list` |
| List operators > NAME > Attach | `operator attach NAME` |
| List operators > NAME > Stop | `operator stop NAME` |
| List operators > NAME > Start | `operator start NAME` |
| List operators > NAME > Start and attach | `operator start NAME --attach` |
| List operators > NAME > Rename > type NEW > Enter | `operator rename NAME NEW` |
| List operators > NAME > Delete > y | `operator delete NAME --yes` |
| Recover operator sessions > Space on each NAME > Enter | `operator recover NAME ...` |

### Only on the command line

| Command line | Why the menu lacks it |
| --- | --- |
| `operator handoff` with `--status`, `--next`, `--context`, `--instance` and `--no-restart` | The agent inside an operator's session runs it. Nobody hands off from the menu. |
| `operator doctor` | It checks that this machine can run operator, before the first start. |
| `--fresh` | It starts again at session 1. The menu always resumes the last session. |
| `--agent`, any other Copilot option, and a task | The menu asks only for a name. |
| `--all` | The recover screen has you pick each operator. |
| `--home` | The menu uses `COPILOT_OPERATOR_HOME`, or `~/.operator` when that is unset. |
| `operator help`, `--help` and `-h` | The menu lists its own choices. |

### Only in the menu

| Menu | Why the command line lacks it |
| --- | --- |
| Quit | A typed command ends by itself. |

### Where they behave differently

- Start an operator refuses a name that an operator in another directory has, and asks again. `operator start NAME` starts that operator in its own directory. `operator start` with no name exits 2 and asks for a name when that operator has this directory's name.
- With several operators in this directory, Start an operator leaves the name empty and asks for one. `operator start` with no name lists them and exits 2.
- The list screen offers Attach and Stop for a running operator, and Start, Start and attach, Rename and Delete for a stopped one. For a running operator, `operator rename` renames it, `operator start NAME --attach` attaches, and `operator start NAME` and `operator delete` refuse. For a stopped operator, `operator attach` refuses and `operator stop` prints "stop requested for NAME" with nothing to stop.
- `operator list` with no operators says "No operators yet. Start one with: operator start". List operators shows `(none)` under both headings.
- `operator recover` with no names lists the operators that need recovering, or says none do. The menu shows how many on its main menu row.
- A choice that keeps the menu open shows the command's message on the screen and has no exit code. The typed command prints the message and exits non-zero when it fails.

## Verbs

```
operator doctor               check that this machine can run operator
operator start [NAME] [TASK]  start a supervised operator
operator list                 list operators
operator attach NAME          attach this terminal to a running operator
operator stop NAME            ask an operator's supervisor to stop
operator rename NAME NEW      rename an operator
operator delete NAME [--yes]  delete an operator and its settings
operator recover              list operators that need recovering after a crash
operator recover NAME ...     bring the named operators back
operator recover --all        bring every one of them back
operator handoff --status "what you did" [--next "what is next"]
```

NAME can also be the operator's id. A command that operator prints for you to type uses the id when a shell would change the name even inside quotes, as it would a name holding `$` or `%`.

`operator start` without NAME starts the operator already working in this directory, or creates one named after the directory. When several work here, it lists them and exits 2. `--attach` attaches this terminal once the operator has a session, and exits 1 when none appears within 2 seconds, as between two sessions. When the operator is already running, `--attach` attaches to it. With `--fresh`, a Copilot option or a task, it says the operator is already running and exits 1.

Words after NAME are the task, and Copilot receives them inside its opening prompt. With `--name`, every word is the task. A word that follows a Copilot option such as `--model` and does not start with `-` is that option's value. Pass a value that starts with `-` as `--model=VALUE`. After a flag that takes no value, start the task with `--`.

Handoff is for the agent inside the session, not the menu. It writes a handoff file for that operator's own repo and asks the supervisor to start the next session. When to hand off is the repository's business, in AGENTS.md or from the user. The launch preamble only says that a handoff is possible and how to run it.

`operator handoff` identifies the calling session by walking its process ancestry and matching the custody record the runner wrote at launch. It refuses if it cannot find exactly one operator. `--instance` is an optional cross-check, not the address. This is a check against a caller confused about which session it is in. It is not a security boundary against other code running as the same user. Sessions launched before this check existed have no custody record, so stop and restart those operators after upgrading.

## Tests

`python -m pytest -q` runs the suite. CI is Windows and Linux on Python 3.10 and 3.12.
