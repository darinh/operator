"""The five unit-1 scenarios. Labels live on Oracle, never on Program."""
from __future__ import annotations

import json
import sys
from pathlib import Path

from operator_bench.observe import observe
from operator_bench.scenario import (
    BUSYWORK, DETECTION, HANDOFF, LAUNCH_FAIL, MISS, SILENCE,
    STOP, TRUE_NEGATIVE, UNACCOUNTED, WORK, Oracle, Program, Scenario, Session,
)
from operator_bench.score import ScenarioScore, Scorecard, score_run, scorecard
from operator_bench.world import make_world, spawn, wait

_HORIZON = "unit1"
_LABELS = "fixture"


def stall_after_five() -> Scenario:
    s = tuple([Session(0, WORK, HANDOFF)] * 5 + [Session(0, SILENCE, HANDOFF)] * 3)
    return Scenario(Program("stall-after-five", "stall-after-five", s),
                    Oracle(6, 8, False, DETECTION, _LABELS, 8, 3))


def healthy_slow() -> Scenario:
    s = tuple([Session(150, WORK, HANDOFF)] * 4 + [Session(0, SILENCE, STOP)])
    return Scenario(Program("healthy-slow", "healthy-slow", s),
                    Oracle(None, None, True, TRUE_NEGATIVE, _LABELS, 4))


def backlog_0014() -> Scenario:
    s = (Session(0, WORK, HANDOFF), *([Session(0, BUSYWORK, HANDOFF)] * 7),
         Session(0, SILENCE, STOP))
    return Scenario(Program("backlog-0014", "backlog-0014", s),
                    Oracle(2, 8, False, MISS, _LABELS, 8, 3))


def crash_loop() -> Scenario:
    return Scenario(
        Program("crash-loop", "crash-loop", (Session(0, LAUNCH_FAIL, LAUNCH_FAIL),)),
        Oracle(1, 1, False, DETECTION, _LABELS, 1, 1, "MuxSessionError"))


def unaccounted_endings() -> Scenario:
    s = tuple([Session(130, SILENCE, UNACCOUNTED)] * 5)
    return Scenario(Program("unaccounted-endings", "unaccounted-endings", s),
                    Oracle(1, 5, False, DETECTION, _LABELS, 5, 4))


def spend_ceiling() -> Scenario:
    s = tuple([Session(0, WORK, HANDOFF, 1.0)] * 4)
    return Scenario(
        Program("spend-ceiling", "spend-ceiling", s,
                max_virtual_seconds=8_000.0, max_sleeps=8_000),
        Oracle(None, None, False, TRUE_NEGATIVE, _LABELS, 4, spend_ceiling=2.0))


def scenarios() -> tuple[Scenario, ...]:
    return (
        stall_after_five(), healthy_slow(), backlog_0014(),
        crash_loop(), unaccounted_endings(),
    )


def run_one(scenario: Scenario, parent: Path, timeout: float = 90.0) -> ScenarioScore:
    world = make_world(parent)
    try:
        path = world.home / "program.json"
        path.write_text(json.dumps(scenario.program.request()), encoding="utf-8")
        cap = scenario.oracle.spend_ceiling
        extra = None if cap is None else {"OPERATOR_SPEND_CEILING": str(cap)}
        proc = spawn(world, [
            sys.executable, "-m", "operator_bench.child", str(path),
        ], extra)
        wait(proc, timeout)
        obs = observe(world.home)
        return score_run(scenario.program.name, obs, scenario.oracle)
    finally:
        world.close()


def measure(parent: Path | None = None, timeout: float = 90.0) -> Scorecard:
    import shutil
    import tempfile
    owned = parent is None
    root = Path(tempfile.mkdtemp(prefix="operator-bench-")) if owned else Path(parent)
    try:
        return run_suite(root, timeout=timeout)
    finally:
        if owned:
            shutil.rmtree(root, ignore_errors=True)


def run_suite(parent: Path, timeout: float = 90.0) -> Scorecard:
    rows = [run_one(s, Path(parent) / s.program.name, timeout=timeout)
            for s in scenarios()]
    extra = run_one(spend_ceiling(), Path(parent) / "spend-ceiling", timeout)
    return scorecard(tuple(rows) + (extra,), label_source=_LABELS, horizon=_HORIZON)
