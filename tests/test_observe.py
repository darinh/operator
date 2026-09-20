"""Observations come from published artifacts, ledger via real LedgerTail."""
from __future__ import annotations

import ast
import json
import sys

from operator_bench.observe import observe
from operator_bench.scenario import SILENCE, STOP, Program, Session
from operator_bench.world import make_world, repo_root, spawn, wait

REPO = repo_root()


def test_observe_imports_the_real_ledger_tail():
    source = (REPO / "operator_bench" / "observe.py").read_text(encoding="utf-8")
    names = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
            names.update(alias.name for alias in node.names)
    assert "operator_fleet.ledger_tail" in names or "ledger_tail" in names
    assert "LedgerTail" in names


def test_observe_reads_the_sandbox_ledger_after_a_real_run(tmp_path):
    from ledger_tail import LedgerTail

    world = make_world(tmp_path)
    program = Program(
        name="observe", instance="bench",
        sessions=(Session(0, SILENCE, STOP),),
    )
    path = world.home / "program.json"
    path.write_text(json.dumps(program.request()), encoding="utf-8")
    proc = spawn(world, [sys.executable, "-m", "operator_bench.child", str(path)])
    assert wait(proc, 60) == 0
    obs = observe(world.home)
    assert obs.exit_code == 0
    assert obs.error is None
    events = [r.get("event") for r in obs.records]
    assert "supervisor_start" in events
    assert obs.chain == "verified"
    assert obs.polls >= 0
    assert (world.home / "bench-tail.json").exists()
    tail = LedgerTail(world.home / "trace.jsonl", world.home / "other-tail.json")
    assert tail.read()
