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

The main menu is Start an operator, List operators, Recover operator sessions when any need it, and Quit. When none need recovery, that row says so and selecting it stays on the menu. After an action you return to the screen you came from. Attach, and Start and attach, are the exceptions. They leave the menu and take this terminal.

## Verbs

```
operator doctor               check that this machine can run operator
operator start [NAME]         start a supervised operator
operator list                 list operators
operator attach NAME          attach this terminal to a running operator
operator stop NAME            ask an operator's supervisor to stop
operator rename NAME NEW      rename an operator
operator delete NAME [--yes]  delete an operator and its settings
operator recover [NAME ...]   list operators that need recovering after a crash
operator recover --all        bring those supervisors back
operator handoff --instance NAME --status "what you did"
```

Handoff is for the agent inside the session, not the menu. It writes a handoff file and asks the supervisor to start the next session. When to hand off is the repository's business, in AGENTS.md or from the user. The launch preamble only says that a handoff is possible and how to run it.

## Tests

`python -m pytest -q` runs the suite. CI is Windows and Linux on Python 3.10 and 3.12.
