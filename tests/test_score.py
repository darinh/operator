"""Estimate is a sum type. Measurement has no bare-float constructor."""
from __future__ import annotations

import math

import pytest

from operator_bench.observe import Observation
from operator_bench.scenario import (
    DETECTION, FALSE_ALARM, MISS, TRUE_NEGATIVE, Oracle,
)
from operator_bench.score import (
    Estimated, Exact, Latency, Measurement, NoEstimate, Scorecard,
    WILSON, classify, format_measurement, format_scorecard, kernel_stopped,
    rate, score_run, scorecard, wilson,
)


def _obs(**kwargs) -> Observation:
    fields = dict(
        exit_code=0, error=None, records=(), session_exits=(),
        polls=10, launch_polls=(0, 4, 8), virtual_seconds=100.0,
        sleeps=20, log_text="",
    )
    fields.update(kwargs)
    return Observation(**fields)


def test_wilson_interval_sits_around_the_rate():
    p, lo, hi = wilson(5, 10)
    assert p == 0.5
    assert 0.0 <= lo < p < hi <= 1.0
    p0, lo0, hi0 = wilson(0, 1)
    assert p0 == 0.0
    assert lo0 == 0.0
    assert hi0 > 0.0
    p1, lo1, hi1 = wilson(1, 1)
    assert p1 == 1.0
    assert lo1 < 1.0
    assert hi1 == 1.0


def test_wilson_rejects_empty_n():
    with pytest.raises(ValueError):
        wilson(0, 0)


def test_rate_is_estimated_not_a_bare_float():
    m = rate(1, 2, eligible=2, censored=0, label_source="fixture", horizon="unit1")
    assert isinstance(m.estimate, Estimated)
    assert m.n == 2
    assert m.eligible == 2
    assert m.censored == 0
    assert m.coverage == 1.0
    assert m.label_source == "fixture"
    assert m.horizon == "unit1"


def test_rate_without_eligible_trials_is_noestimate():
    m = rate(0, 3, eligible=0, censored=3, label_source="fixture", horizon="unit1")
    assert isinstance(m.estimate, NoEstimate)
    assert "eligible" in m.estimate.reason


def test_measurement_rejects_a_bare_float():
    with pytest.raises(TypeError):
        Measurement(
            estimate=0.5, n=1, eligible=1, censored=0,
            coverage=1.0, label_source="x", horizon="y",
        )


def test_latency_carries_its_censoring_bound():
    missed = Latency(polls=None, censored=True, bound_polls=40)
    assert missed.censored
    assert missed.polls is None
    assert missed.bound_polls == 40


def test_classify_miss_and_false_alarm_are_distinct():
    required = Oracle(
        stalled_from=2, stop_required_by=8, any_stop_is_false_alarm=False,
        expected=MISS, label_source="fixture", horizon_sessions=8,
    )
    control = Oracle(
        stalled_from=None, stop_required_by=None, any_stop_is_false_alarm=True,
        expected=TRUE_NEGATIVE, label_source="fixture", horizon_sessions=8,
    )
    assert classify(_obs(exit_code=0), required) == MISS
    assert classify(_obs(exit_code=3), required) == DETECTION
    assert classify(_obs(exit_code=0), control) == TRUE_NEGATIVE
    assert classify(_obs(exit_code=3), control) == FALSE_ALARM


def test_kernel_stopped_reads_breaker_exit_codes():
    assert kernel_stopped(_obs(exit_code=3))
    assert kernel_stopped(_obs(exit_code=4))
    assert not kernel_stopped(_obs(exit_code=0))
    assert kernel_stopped(_obs(exit_code=1, error="MuxSessionError: x"))


def test_scorecard_has_no_combined_accuracy_field():
    row = score_run("demo", _obs(exit_code=3), Oracle(
        stalled_from=1, stop_required_by=3, any_stop_is_false_alarm=False,
        expected=DETECTION, label_source="fixture", horizon_sessions=3,
    ))
    card = scorecard((row,), label_source="fixture", horizon="unit1")
    assert isinstance(card, Scorecard)
    names = set(card.__dataclass_fields__)
    assert "miss_rate" in names
    assert "false_alarm_rate" in names
    for banned in ("accuracy", "score", "pass_rate"):
        assert banned not in names


def test_format_measurement_names_wilson_and_never_prints_a_bare_float():
    m = rate(1, 2, eligible=2, censored=0, label_source="fixture", horizon="unit1")
    text = format_measurement("miss_rate", m)
    assert WILSON in text
    assert "n=2" in text
    assert "coverage=" in text
    assert "labels=fixture" in text
    assert "horizon=unit1" in text
    empty = rate(0, 0, eligible=0, censored=0, label_source="fixture", horizon="unit1")
    assert "NoEstimate" in format_measurement("miss_rate", empty)


def test_detection_latency_is_polls_from_stall_start():
    oracle = Oracle(
        stalled_from=2, stop_required_by=5, any_stop_is_false_alarm=False,
        expected=DETECTION, label_source="fixture", horizon_sessions=5,
    )
    row = score_run("stall", _obs(exit_code=3, polls=20, launch_polls=(0, 7, 14)),
                    oracle)
    assert row.outcome == DETECTION
    assert row.latency.polls == 13
    assert not row.latency.censored
    card = scorecard((row,), label_source="fixture", horizon="unit1")
    assert isinstance(card.detection_latency.estimate, Exact)
    assert card.detection_latency.estimate.value == 13


def test_format_scorecard_is_ascii():
    row = score_run("demo", _obs(exit_code=3), Oracle(
        stalled_from=1, stop_required_by=3, any_stop_is_false_alarm=False,
        expected=DETECTION, label_source="fixture", horizon_sessions=3,
    ))
    text = format_scorecard(scorecard((row,), label_source="fixture", horizon="unit1"))
    text.encode("ascii")
    assert "miss_rate" in text
    assert "false_alarm_rate" in text
    assert math.isfinite(row.latency.bound_polls)
