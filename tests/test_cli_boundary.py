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
MAX_CLI_CODE_LINES = 1276
MAX_CLI_TOTAL_LINES = 1630


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
