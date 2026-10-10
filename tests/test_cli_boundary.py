"""The CLI stays a thin front door. The number is lowered after the cut."""
from __future__ import annotations

from pathlib import Path

from test_kernel_boundary import (MAX_MODULE_CODE_LINES, MAX_MODULE_LINES,
                                  REPO, code_lines)

CLI = REPO / "operator_cli"
# Raised from 737/983 when start, rename, and delete landed beside attach.
# The verb bodies live in lifecycle.py so no module crosses the per-module
# ceiling. 927 and 1179 are the measured sizes.
# Raised from 927/1179 when handoff resolves a name, delete keeps the project
# if a sibling record cannot be read, and start checks the name and the cwd
# before it spawns. 967 and 1222 are the measured sizes.
# Raised from 967/1222 when the numbered menu was replaced by a keyboard menu.
# The old prompts lived in entry.py and verbs.py and were deleted. The decoder
# (keys.py) and the screens (menu.py) are new modules, and no module crosses
# the per-module ceiling. 1266 and 1610 are the measured sizes.
# Raised from 1266/1610 when handoff refuses a whitespace status, an unknown
# option, and a value that looks like a flag. 1279 and 1632 are the measured
# sizes.
# Raised from 1279/1632 when handoff derives the operator from process custody
# and treats `--instance` as a cross-check. 1288 and 1641 are the measured sizes.
# Lowered from 1288/1641 when the quoting helpers nothing has called since the
# keyboard menu were deleted and start took its free words as the task. 1284
# and 1632 are the measured sizes.
# Lowered from 1284/1632 when the start screen became one name box that leaves
# the menu to start and attach. The create question, the menu's own name
# checks and its copy of the default name were deleted. Start then learned to
# wait for a session before it attaches, to refuse a blank name, and to ask
# which operator to start when several work in one directory. 1277 and 1632
# are the measured sizes.
# Lowered from 1277/1632 when recover's handler became recover.main itself, so
# the menu and the typed command share it. 1275 and 1628 are the measured sizes.
# Raised to 1276/1630 for HELP_WORDS, which names the help words once so that a
# test can require a case for each. Both are the measured sizes.
# Lowered to 1276/1628 when dispatch's docstring stopped claiming the menu goes
# through it. Both are the measured sizes.
# Raised to 1279/1634 when the commands operator prints quote a name that is
# not one plain word, so a name with a space pastes back as one argument. Both
# are the measured sizes.
# Raised to 1284/1643 when those commands type an operator by its id if a shell
# would change its name even inside quotes, and the list of operators in one
# directory shows the name beside that id. Both are the measured sizes.
# Raised to 1287/1646 when the main menu's recover row and its Quit test became
# statements, so the walk's line trace sees each way through them. Both are the
# measured sizes.
# Raised to 1293/1656 when List operators shows the records it could not read,
# as `operator list` does. listing.load reads them once for both. Both are the
# measured sizes.
# Raised to 1298/1662 when delete keeps a project while an operator works in
# any checkout of it, comparing primary checkouts rather than raw directories,
# and keeps it while a remaining operator's checkout is gone and cannot say
# which project it was. Both are the measured sizes.
# Raised to 1320/1694 when start records who asked for an operator and list
# draws each child under its parent. Both are the measured sizes.
# Raised to 1427/1828 when an operator may act only on its own children, within
# two caps, and start learned --dir. The rules live in the new family.py so
# lifecycle.py stays under the per-module ceiling. Both are the measured sizes.
# Raised to 1511/1926 when the new messaging.py added `operator send` and
# `operator inbox`, and list learned to say when mail waits for the person.
# Both are the measured sizes.
# Raised to 1531/1966 when two operators starting children at once had to take
# turns, so both cannot take a parent's last place, and a child still starting
# counts toward the cap; when the catalog's lock became a file lock any verb
# can hold; and when list stopped telling an operator about the person's mail.
# Both are the measured sizes.
# Raised to 1628/2093 when the menu learned messaging: a Messaging row with the
# person's count, Inbox and Send a message on the same handlers as `operator
# inbox` and `operator send`, a Message Log of every message in the home, and
# lists and text that scroll when longer than the terminal. Both are the
# measured sizes.
#
# Raised to 1635/2104 so the menu counts and offers mail for whoever runs it,
# as `operator inbox` and `operator send` decide the caller, shows Send's
# unreadable operators, and scrolls the recover list too. Both are the
# measured sizes.
#
# Raised to 1642/2114 so the menu shows (?) for its count and lists no one to
# message when it cannot tell who runs it, where `operator inbox` and
# `operator send` refuse. Both are the measured sizes.
# Lowered to 1632/2096 when the menu stopped working out who runs it. It
# serves a person, and an operator's agent uses the typed commands. Both are
# the measured sizes.
MAX_CLI_CODE_LINES = 1632
MAX_CLI_TOTAL_LINES = 2096


def cli_modules() -> list[Path]:
    return sorted(p for p in CLI.glob("*.py") if p.stem != "__init__")


def _measure(paths) -> tuple[int, int]:
    sources = [path.read_text(encoding="utf-8") for path in paths]
    return (sum(code_lines(s) for s in sources),
            sum(len(s.splitlines()) for s in sources))


def test_the_cli_budget_scan_sees_the_package():
    assert len(cli_modules()) >= 1
    assert _measure(cli_modules())[0] > 50


def test_the_cli_package_stays_under_its_budget():
    code, total = _measure(cli_modules())
    assert code <= MAX_CLI_CODE_LINES, (
        f"operator_cli is {code} code lines, budget {MAX_CLI_CODE_LINES}.")
    assert total <= MAX_CLI_TOTAL_LINES, (
        f"operator_cli is {total} lines, budget {MAX_CLI_TOTAL_LINES}.")


def test_no_cli_module_exceeds_the_per_module_ceilings():
    for path in cli_modules():
        source = path.read_text(encoding="utf-8")
        assert code_lines(source) <= MAX_MODULE_CODE_LINES, path.name
        assert len(source.splitlines()) <= MAX_MODULE_LINES, path.name
