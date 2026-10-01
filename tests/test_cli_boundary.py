"""The CLI stays a thin front door. The number is lowered after the cut."""
from __future__ import annotations

from pathlib import Path

from test_kernel_boundary import (MAX_MODULE_CODE_LINES, MAX_MODULE_LINES,
                                  REPO, code_lines)

CLI = REPO / "operator_cli"
MAX_CLI_CODE_LINES = 782
MAX_CLI_TOTAL_LINES = 1061


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
