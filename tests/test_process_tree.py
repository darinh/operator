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


def test_a_windows_parent_born_after_its_child_ends_the_chain(monkeypatch):
    """ToolHelp keeps a dead parent's pid. Once Windows reuses it, the walk
    would carry on into a stranger's tree, which can hold another operator."""
    import process_tree
    table = {10: 20, 20: 30, 30: 40, 40: 50}
    born = {10: 500, 20: 400, 30: 900, 40: 100, 50: 50}
    monkeypatch.setattr(process_tree, "IS_WINDOWS", True)
    monkeypatch.setattr(process_tree, "_win_parents", lambda: table)
    monkeypatch.setattr(process_tree, "_win_created", born.get)
    assert process_tree.ancestry(10) == [20]


def test_a_windows_parent_that_is_gone_ends_the_chain(monkeypatch):
    import process_tree
    table = {10: 20, 20: 30, 30: 40}
    born = {10: 500, 20: 400, 40: 100}
    monkeypatch.setattr(process_tree, "IS_WINDOWS", True)
    monkeypatch.setattr(process_tree, "_win_parents", lambda: table)
    monkeypatch.setattr(process_tree, "_win_created", born.get)
    assert process_tree.ancestry(10) == [20]
