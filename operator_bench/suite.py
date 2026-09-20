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

_AGENT = ("--agent", "bench:seat")
_HORIZON = "unit1"
_LABELS = "fixture"


def stall_after_five() -> Scenario:
    sessions = tuple(
        [Session(0, WORK, HANDOFF)] * 5 + [Session(0, SILENCE, HANDOFF)] * 3
    )
    return Scenario(
        Program("stall-after-five", "stall-after-five", sessions),
        Oracle(stalled_from=6, stop_required_by=8, any_stop_is_false_alarm=False,
               expected=DETECTION, label_source=_LABELS, horizon_sessions=8,
               expected_exit=3),
    )


def healthy_slow() -> Scenario:
    sessions = tuple(
        [Session(150, WORK, HANDOFF)] * 4 + [Session(0, SILENCE, STOP)]
    )
    return Scenario(
        Program("healthy-slow", "healthy-slow", sessions),
        Oracle(stalled_from=None, stop_required_by=None, any_stop_is_false_alarm=True,
               expected=TRUE_NEGATIVE, label_source=_LABELS, horizon_sessions=4),
    )


def backlog_0014() -> Scenario:
    sessions = (
        Session(0, WORK, HANDOFF),
        *([Session(0, BUSYWORK, HANDOFF)] * 7),
        Session(0, SILENCE, STOP),
    )
    return Scenario(
        Program("backlog-0014", "backlog-0014", sessions),
        Oracle(stalled_from=2, stop_required_by=8, any_stop_is_false_alarm=False,
               expected=MISS, label_source=_LABELS, horizon_sessions=8,
               expected_exit=3),
    )


def crash_loop() -> Scenario:
    sessions = (Session(0, LAUNCH_FAIL, LAUNCH_FAIL),)
    return Scenario(
        Program("crash-loop", "crash-loop", sessions),
        Oracle(stalled_from=1, stop_required_by=1, any_stop_is_false_alarm=False,
               expected=DETECTION, label_source=_LABELS, horizon_sessions=1,
               expected_exit=1),
    )


def unaccounted_endings() -> Scenario:
    sessions = tuple([Session(130, SILENCE, UNACCOUNTED)] * 5)
    return Scenario(
        Program("unaccounted-endings", "unaccounted-endings", sessions),
        Oracle(stalled_from=1, stop_required_by=5, any_stop_is_false_alarm=False,
               expected=DETECTION, label_source=_LABELS, horizon_sessions=5,
               expected_exit=4),
    )


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
        proc = spawn(world, [
            sys.executable, "-m", "operator_bench.child", str(path),
        ])
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
    rows = []
    for scenario in scenarios():
        rows.append(run_one(scenario, Path(parent) / scenario.program.name,
                            timeout=timeout))
    return scorecard(tuple(rows), label_source=_LABELS, horizon=_HORIZON)
