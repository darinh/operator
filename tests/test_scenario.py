"""Oracle labels never cross the process boundary into the child."""
from __future__ import annotations

import ast
import json
import sys

from operator_bench.scenario import (
    MISS, SILENCE, STOP, Oracle, Program, Session,
)
from operator_bench.world import make_world, repo_root, spawn, wait

REPO = repo_root()


def test_request_payload_keys_are_only_the_child_contract():
    program = Program(
        name="demo", instance="bench",
        sessions=(Session(0, SILENCE, STOP),),
    )
    payload = program.request()
    assert set(payload) == {
        "instance", "user_args", "max_virtual_seconds", "max_sleeps", "sessions",
    }
    dumped = json.dumps(payload)
    for field in ("stalled_from", "stop_required_by", "any_stop_is_false_alarm",
                  "expected", "label_source", "horizon_sessions", "oracle"):
        assert field not in dumped


def test_oracle_cannot_reach_the_child(tmp_path):
    """Inspect what actually crosses the process boundary, not the types."""
    program = Program(
        name="boundary", instance="bench",
        sessions=(Session(0, SILENCE, STOP),),
    )
    oracle = Oracle(
        stalled_from=2, stop_required_by=8, any_stop_is_false_alarm=False,
        expected=MISS, label_source="fixture", horizon_sessions=8,
    )
    world = make_world(tmp_path)
    request_path = world.home / "program.json"
    request_path.write_text(json.dumps(program.request()), encoding="utf-8")
    proc = spawn(world, [
        sys.executable, "-m", "operator_bench.child", str(request_path),
    ])
    rc = wait(proc, 60)
    assert rc == 0
    crossed = json.loads(request_path.read_text(encoding="utf-8"))
    assert "oracle" not in crossed
    wire = json.dumps(crossed)
    for field in vars(oracle):
        assert field not in wire
    child_src = (REPO / "operator_bench" / "child.py").read_text(encoding="utf-8")
    assert "Oracle" not in child_src


def test_child_source_cannot_name_the_oracle():
    source = (REPO / "operator_bench" / "child.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    assert "Oracle" not in names
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and node.value == "Oracle":
            raise AssertionError("child carries the Oracle type name")
