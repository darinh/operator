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

Each menu choice that runs a command calls the same handler as the typed command, and `tests/test_surfaces.py` checks that they are the same functions. List operators splits operators with the function `operator list` uses. The same test file runs each choice both ways in a sandbox. Both ways must spawn, stop, recover and attach the same operators, leave the same operators and projects on disk, and tell the user the same thing. The test also fails when a command, an option or a menu item has no case, when the tables below disagree with the cases, when a row of a one-sided table gives no reason, and when the bullets under Where they behave differently are not, word for word, the differences the tests record. No test can tell whether a reason or a bullet is true. A command escapes when `operator` answers a word that no string in `operator_cli` spells, such as one built from pieces or imported from another package. It finds menu items by walking the menu in four fake states. A choice or a command none of them shows can still escape when an index computed from data picks it from labels the menu draws, or arguments it passes, elsewhere, and so can a key or any other branch inside the menu's input loops. An option escapes when it is built from pieces or when code outside `operator_cli` parses it. The option scan skips `menu.py`, which builds the arguments for the verbs, and `supervise.py`, which parses what `operator start` hands the supervisor process it spawns, so an option only they spell needs no case. One that `operator_cli` imports from another package fails a test when it sits, however deep, in a collection, in an object's `__dict__`, or in an attribute or slot that a class of this repository declares. That test also fails on a spelling `operator_cli` imports and never parses. It escapes when it exists only once code has run, as a property's value does, sits anywhere else, such as in a function's defaults or closure or a `functools.partial`'s arguments, or is reached by a name built when the code runs.

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
| `--dir` | The menu starts an operator in the directory you stand in. |
| `operator send` | Mail goes between an operator and its parent or child, and agents send it from their sessions. |
| `operator inbox` | It prints mail and files it as read, and the menu has no screen for reading text. |
| `operator help`, `--help` and `-h` | The menu lists its own choices. |

### Only in the menu

| Menu | Why the command line lacks it |
| --- | --- |
| Quit | A typed command ends by itself. |

### Where they behave differently

- Start an operator refuses a name that an operator in another directory has, and asks again. `operator start NAME` starts that operator in its own directory. `operator start` with no name exits 2 and asks for a name when that operator has this directory's name.
- With several operators in this directory, Start an operator leaves the name empty and asks for one. `operator start` with no name lists them and exits 2.
- Start an operator refuses a name that starts with `-` and asks again. `operator start -x` takes `-x` as a Copilot option and starts this directory's operator. `operator start --name=-x` refuses it as the menu does.
- The list screen offers Attach and Stop for a running operator, and Start, Start and attach, Rename and Delete for a stopped one. For a running operator, `operator rename` renames it, `operator start NAME --attach` attaches, and `operator start NAME` and `operator delete` refuse. For a stopped operator, `operator attach` refuses and `operator stop` prints "stop requested for NAME" with nothing to stop.
- `operator list` with no operators says "No operators yet. Start one with: operator start". List operators shows `(none)` under both headings.
- `operator list` ends by saying how many messages wait for you, when some do. List operators does not say. Read them with `operator inbox`.
- Delete on the list screen asks "Delete? [y/N]" and deletes on the key y or Y. `operator delete NAME` without `--yes` asks the same in a terminal and deletes on y or yes, in any case, then Enter. Without a terminal it exits 2 and says to pass `--yes`.
- When it cannot read the operators directory, `operator list` says "could not read operators" and exits 1. List operators says so above `(none)` under both headings.
- `operator recover` with no names lists the operators that need recovering, or says none do. The menu shows how many on its main menu row.
- A choice that keeps the menu open shows the command's message on the screen and has no exit code. The typed command prints the message and exits non-zero when it fails.

## Verbs

```
operator doctor               check that this machine can run operator
operator start [NAME] [TASK]  start a supervised operator
operator start NAME --dir DIR start one in another worktree of this repository
operator list                 list operators
operator attach NAME          attach this terminal to a running operator
operator stop NAME            stop an operator and every operator under it
operator rename NAME NEW      rename an operator
operator delete NAME [--yes]  delete an operator and its settings
operator recover              list operators that need recovering after a crash
operator recover NAME ...     bring the named operators back
operator recover --all        bring every one of them back
operator handoff --status "what you did" [--next "what is next"]
operator send NAME TEXT       message an operator's parent or child
operator inbox                read the messages sent to you
```

NAME can also be the operator's id. operator ignores spaces around NAME, as it does when it stores a name, so `" alpha "` means `alpha` in every verb. A command that operator prints for you to type uses the id when a shell would change the name even inside quotes, as it would a name holding `$` or `%`.

`operator start` without NAME starts the operator already working in this directory, or creates one named after the directory. When several work here, it lists them and exits 2. `--attach` attaches this terminal once the operator has a session, and exits 1 when none appears within 2 seconds, as between two sessions. When the operator is already running, `--attach` attaches to it. With `--fresh`, a Copilot option or a task, it says the operator is already running and exits 1.

Words after NAME are the task, and Copilot receives them inside its opening prompt. With `--name`, every word is the task. A word that follows a Copilot option such as `--model` and does not start with `-` is that option's value. Pass a value that starts with `-` as `--model=VALUE`. After a flag that takes no value, start the task with `--`.

Handoff is for the agent inside the session, not the menu. It writes a handoff file for that operator's own repo and asks the supervisor to start the next session. When to hand off is the repository's business, in AGENTS.md or from the user. The launch preamble says that a handoff is possible and how to run it. It also names the operator, its parent and its children, how to start and stop children, and how to message its parent and children.

`operator handoff` identifies the calling session by walking its process ancestry and matching the custody record the runner wrote at launch. It refuses if it cannot find exactly one operator. `--instance` is an optional cross-check, not the address. This is a check against a caller confused about which session it is in. It is not a security boundary against other code running as the same user. Sessions launched before this check existed have no custody record, so stop and restart those operators after upgrading.

`operator start` uses the same walk to record who asked. When it runs inside an operator's session, the new operator is that operator's child. Otherwise a person started it. `operator list` draws each child indented under its parent. A child whose parent was deleted lists at the top. Records written before operators had parents read as started by a person. Because the walk stops at a process whose parent has exited, a command an agent launches detached reads as a person's.

An operator may start, stop and delete only its own children, and must name each child it starts. It cannot attach, rename or recover. Those are a person's commands. `operator recover` with no names only lists, so an operator may run it. A child starts in its parent's directory, wherever the agent's shell stands. `--dir DIR` starts an operator in another worktree of the same repository. When DIR is not one, it exits 2 and prints the `git worktree add` command that makes it. Outside a git repository, `--dir` always exits 2. An operator may run 4 children at once, and operators nest 3 deep, counting the one a person started. `OPERATOR_MAX_CHILDREN` and `OPERATOR_MAX_DEPTH` change those limits. A person has no limit on the operators they start. `operator stop NAME` also stops every running operator under NAME, and names each one. Deleting an operator leaves its children, which then list at the top.

`operator send NAME TEXT` posts a message to NAME's mailbox under the operator home. Mail goes only between a parent and its child, either way. A person is the parent of each operator they start, so a person sends only to those, and such an operator reaches the person with `operator send human TEXT`. A message holds at most 4000 characters. The supervisor of a running operator types each waiting message into its session as one line, about every 10 seconds, starting `[operator message from` and naming the sender. Newlines become spaces. Mail to a stopped operator waits until it starts again. Delivery is at least once, so a supervisor that dies between typing a message and filing it types that message again. `operator inbox` prints the caller's waiting mail and files it as read. A person reads mail from the operators they started this way, and `operator list` says when some is waiting. Deleting an operator deletes its mailbox.

## Tests

`python -m pytest -q` runs the suite. CI is Windows and Linux on Python 3.10 and 3.12.
