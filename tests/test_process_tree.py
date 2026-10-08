"""Process ancestry, against the live process table. No mocks."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest


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


def test_a_cycle_back_to_the_caller_does_not_list_the_caller(monkeypatch):
    import process_tree
    monkeypatch.setattr(process_tree, "IS_WINDOWS", True)
    monkeypatch.setattr(process_tree, "_win_parents", lambda: {10: 20, 20: 10})
    monkeypatch.setattr(process_tree, "_win_created", {10: 500, 20: 500}.get)
    assert process_tree.ancestry(10) == [20]


def test_a_windows_parent_that_is_gone_ends_the_chain(monkeypatch):
    import process_tree
    table = {10: 20, 20: 30, 30: 40}
    born = {10: 500, 20: 400, 40: 100}
    monkeypatch.setattr(process_tree, "IS_WINDOWS", True)
    monkeypatch.setattr(process_tree, "_win_parents", lambda: table)
    monkeypatch.setattr(process_tree, "_win_created", born.get)
    assert process_tree.ancestry(10) == [20]


NAMES = {20: (30, "python.exe"), 30: (40, "Operator.EXE"), 40: (50, "pwsh.exe"),
         35: (40, "python.exe"), 45: (50, "python3.12.exe")}


@pytest.mark.parametrize("windows, argv0, chain, ran", [
    (True, r"C:\venv\Scripts\operator", [20, 30, 40], 40),
    (True, r"C:\venv\Scripts\operator.exe", [45, 30, 35], 35),
    (True, r"C:\repo\operator_cli\__main__.py", [35, 40], 35),
    (True, r"C:\venv\Scripts\operator", [40, 50], 40),
    (True, r"C:\venv\Scripts\operator", [40, 30, 35], 40),
    (True, r"C:\venv\Scripts\operator", [20, 30], 20),
    (False, "/venv/bin/operator", [20, 30, 40], 20),
    (True, r"C:\venv\Scripts\operator", [], 0),
])
def test_what_ran_the_command_is_past_its_own_launcher(monkeypatch, windows, argv0,
                                                         chain, ran):
    """A launcher and the Pythons it starts end with the command. A Python
    that ran operator.exe, or ran us with -m, is what started it. The argv0
    values are Windows paths, read as such on any host, so CI on Linux runs
    these too."""
    import process_tree
    monkeypatch.setattr(process_tree, "IS_WINDOWS", windows)
    monkeypatch.setattr(process_tree, "_win_table", lambda: NAMES)
    monkeypatch.setattr(sys, "argv", [argv0])
    assert process_tree.shell(chain) == ran
