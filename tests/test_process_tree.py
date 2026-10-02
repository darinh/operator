"""Process ancestry, against the live process table. No mocks."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def test_a_child_process_sees_this_process_in_its_ancestry():
    """The check handoff depends on. A mock of `ancestry` cannot catch a
    walk that never leaves this process."""
    kernel = Path(__file__).resolve().parent.parent / "operator_kernel"
    code = (
        "import os, process_tree\n"
        "chain = process_tree.ancestry(os.getpid())\n"
        "assert chain is not None\n"
        "print(','.join(str(pid) for pid in chain))\n"
    )
    env = os.environ.copy()
    env["PYTHONPATH"] = str(kernel)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    proc = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=30, env=env,
    )
    assert proc.returncode == 0, proc.stderr
    pids = [int(part) for part in proc.stdout.strip().split(",") if part]
    assert os.getpid() in pids
