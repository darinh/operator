"""python -m operator_bench measure is the public CLI."""
from __future__ import annotations

import subprocess
import sys

from operator_bench.__main__ import main


def test_usage_without_measure():
    assert main([]) == 2
    assert main(["help"]) == 2


def test_module_cli_lists_usage():
    proc = subprocess.run(
        [sys.executable, "-m", "operator_bench"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    assert proc.returncode == 2
    assert "measure" in proc.stdout
