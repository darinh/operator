"""The five scenarios are reachable against the real breaker limits."""
from __future__ import annotations

import op

from operator_bench.scenario import (
    BUSYWORK, DETECTION, HANDOFF, LAUNCH_FAIL, MISS, SILENCE, STOP,
    TRUE_NEGATIVE, UNACCOUNTED, WORK,
)
from operator_bench.suite import (
    backlog_0014, crash_loop, healthy_slow, run_one, scenarios,
    stall_after_five, unaccounted_endings,
)


def test_the_suite_is_exactly_the_five_named_scenarios():
    names = [s.program.name for s in scenarios()]
    assert names == [
        "stall-after-five", "healthy-slow", "backlog-0014",
        "crash-loop", "unaccounted-endings",
    ]


def test_stall_after_five_reaches_the_nochange_breaker():
    sessions = stall_after_five().program.sessions
    work = [s for s in sessions if s.effect == WORK]
    silence = [s for s in sessions if s.effect == SILENCE]
    assert len(work) == 5
    assert len(silence) >= op.MAX_NOCHANGE_SESSIONS
    assert all(s.ending == HANDOFF for s in sessions)
    assert stall_after_five().oracle.stalled_from == 6
    assert stall_after_five().oracle.stop_required_by == 5 + op.MAX_NOCHANGE_SESSIONS


def test_healthy_slow_outlasts_the_crash_and_nochange_breakers():
    sessions = [s for s in healthy_slow().program.sessions if s.ending != STOP]
    assert len(sessions) > op.MAX_NOCHANGE_SESSIONS
    assert all(s.effect == WORK for s in sessions)
    assert all(s.duration_s >= op.HEALTHY_SESSION_SECONDS for s in sessions)
    assert healthy_slow().oracle.any_stop_is_false_alarm


def test_crash_loop_is_launch_failure_not_a_short_session():
    sessions = crash_loop().program.sessions
    assert sessions
    assert all(s.effect == LAUNCH_FAIL for s in sessions)
    assert all(s.duration_s < op.HEALTHY_SESSION_SECONDS for s in sessions)


def test_unaccounted_endings_reach_the_unaccounted_breaker_after_healthy_uptime():
    sessions = unaccounted_endings().program.sessions
    assert len(sessions) >= op.MAX_UNACCOUNTED_SESSIONS
    assert all(s.effect == SILENCE for s in sessions)
    assert all(s.ending == UNACCOUNTED for s in sessions)
    assert all(s.duration_s >= op.HEALTHY_SESSION_SECONDS for s in sessions)


def test_backlog_0014_keeps_moving_the_fingerprint():
    sessions = [s for s in backlog_0014().program.sessions if s.ending != STOP]
    assert sessions[0].effect == WORK
    assert all(s.effect == BUSYWORK for s in sessions[1:])
    assert len(sessions) >= backlog_0014().oracle.stop_required_by


def test_stall_after_five_is_a_detection(tmp_path):
    row = run_one(stall_after_five(), tmp_path)
    assert row.outcome == DETECTION
    assert row.latency.polls is not None
    assert not row.latency.censored


def test_healthy_slow_records_no_false_alarm(tmp_path):
    row = run_one(healthy_slow(), tmp_path)
    assert row.outcome == TRUE_NEGATIVE
    assert row.exit_code == 0


def test_0014_is_still_missed(tmp_path):
    """When this fails, a breaker has learned to see manufactured work.

    The correct response is to update the baseline, never to weaken this
    assertion. backlog-0014 scoring as a detection would mean the kernel
    started catching junk-file churn. That is a real improvement. Record it.
    Do not edit the fixture so the miss comes back.
    """
    row = run_one(backlog_0014(), tmp_path)
    assert row.outcome == MISS


def test_crash_loop_is_detected(tmp_path):
    row = run_one(crash_loop(), tmp_path)
    assert row.outcome == DETECTION


def test_unaccounted_endings_are_detected(tmp_path):
    row = run_one(unaccounted_endings(), tmp_path)
    assert row.outcome == DETECTION
    assert row.exit_code == op.EXIT_UNACCOUNTED
