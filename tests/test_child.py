"""A child process runs the real supervisor against a scripted seat."""
from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

from operator_bench.world import make_world, repo_root, spawn, wait

REPO = repo_root()


def _program(sessions, **extra) -> dict:
    body = {
        "instance": "bench",
        "user_args": ["--agent", "bench:seat"],
        "max_virtual_seconds": 5_000,
        "max_sleeps": 50_000,
        "sessions": sessions,
    }
    body.update(extra)
    return body


def _run_child(tmp_path, program: dict, timeout: float = 60.0):
    world = make_world(tmp_path)
    path = world.home / "program.json"
    path.write_text(json.dumps(program), encoding="utf-8")
    proc = spawn(world, [sys.executable, "-m", "operator_bench.child", str(path)])
    rc = wait(proc, timeout)
    return world, rc, proc


def test_child_module_does_not_import_the_kernel_at_load():
    source = (REPO / "operator_bench" / "child.py").read_text(encoding="utf-8")
    names = set()
    for node in ast.parse(source).body:
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.add(node.module.split(".")[0])
    kernelish = {
        "config", "supervisor", "mux", "instance", "launch", "breakers",
        "operator_kernel", "operator_fleet",
    }
    assert not (names & kernelish), names & kernelish


def test_child_runs_real_loop_mode_to_a_known_exit(tmp_path):
    real_trace = Path.home() / ".operator" / "trace.jsonl"
    before = real_trace.stat().st_mtime if real_trace.exists() else None
    world, rc, proc = _run_child(tmp_path, _program([
        {"duration_s": 0, "effect": "silence", "ending": "stop"},
    ]))
    stderr = proc.stderr.read() if proc.stderr else ""
    assert rc == 0, stderr
    log = (world.home / "operator.log").read_text(encoding="utf-8")
    assert "loop mode" in log
    assert (world.home / "bench-result.json").exists()
    result = json.loads((world.home / "bench-result.json").read_text(encoding="utf-8"))
    assert result["exit_code"] == 0
    assert result["error"] is None
    assert not (Path.home() / ".operator" / "bench-result.json").exists()
    after = real_trace.stat().st_mtime if real_trace.exists() else None
    assert before == after


def test_child_writes_evidence_only_under_the_sandbox_home(tmp_path):
    world, rc, proc = _run_child(tmp_path, _program([
        {"duration_s": 0, "effect": "silence", "ending": "stop"},
    ]))
    assert rc == 0, proc.stderr.read() if proc.stderr else ""
    trace = world.home / "trace.jsonl"
    assert trace.exists()
    real = Path.home() / ".operator"
    assert world.home.resolve() != real.resolve()
    leaked = real / "restart" / "bench.stopreq"
    assert not leaked.exists()
