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
    ledger_verdict_counts, rate, score_run, scorecard, wilson,
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
    m = rate(0, 0, eligible=0, censored=0, label_source="fixture", horizon="unit1")
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
        expected_exit=3,
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
    assert not kernel_stopped(_obs(exit_code=2, log_text="Giving up after 5"))


def test_scorecard_has_no_combined_accuracy_field():
    row = score_run("demo", _obs(exit_code=3), Oracle(
        stalled_from=1, stop_required_by=3, any_stop_is_false_alarm=False,
        expected=DETECTION, label_source="fixture", horizon_sessions=3,
        expected_exit=3,
    ))
    card = scorecard((row,), label_source="fixture", horizon="unit1")
    assert isinstance(card, Scorecard)
    names = set(card.__dataclass_fields__)
    assert "miss_rate" in names
    assert "false_alarm_rate" in names
    assert "ledger_chain_verified" in names
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
        expected_exit=3,
    )
    row = score_run("stall", _obs(exit_code=3, polls=20, launch_polls=(0, 7, 14)),
                    oracle)
    assert row.outcome == DETECTION
    assert row.latency.polls == 13
    assert not row.latency.censored
    card = scorecard((row,), label_source="fixture", horizon="unit1")
    assert isinstance(card.detection_latency.estimate, Exact)
    assert card.detection_latency.estimate.value == 13


def test_n_is_the_sample_the_interval_was_computed_from():
    required = Oracle(
        stalled_from=1, stop_required_by=3, any_stop_is_false_alarm=False,
        expected=DETECTION, label_source="fixture", horizon_sessions=3,
        expected_exit=3,
    )
    control = Oracle(
        stalled_from=None, stop_required_by=None, any_stop_is_false_alarm=True,
        expected=TRUE_NEGATIVE, label_source="fixture", horizon_sessions=4,
    )
    rows = (
        score_run("d1", _obs(exit_code=3), required),
        score_run("d2", _obs(exit_code=3), required),
        score_run("d3", _obs(exit_code=3), required),
        score_run("miss", _obs(exit_code=0), required),
        score_run("ok", _obs(exit_code=0), control),
    )
    card = scorecard(rows, label_source="fixture", horizon="unit1")
    assert card.miss_rate.n == 4
    assert card.miss_rate.eligible == 4
    assert card.miss_rate.censored == 0
    assert card.miss_rate.coverage == 1.0
    p, lo, hi = wilson(1, 4)
    assert isinstance(card.miss_rate.estimate, Estimated)
    assert card.miss_rate.estimate.value == p
    assert card.miss_rate.estimate.lo == lo
    assert card.miss_rate.estimate.hi == hi
    assert card.false_alarm_rate.n == 1
    assert card.false_alarm_rate.eligible == 1


def test_coverage_is_n_over_eligible_not_the_reverse():
    m = rate(0, 1, eligible=4, censored=3, label_source="fixture", horizon="unit1")
    assert m.n == 1
    assert m.eligible == 4
    assert m.censored == 3
    assert m.coverage == 0.25
    p, lo, hi = wilson(0, 1)
    assert isinstance(m.estimate, Estimated)
    assert m.estimate.value == p
    assert m.estimate.lo == lo
    assert m.estimate.hi == hi


def test_measurement_rejects_n_plus_censored_past_eligible():
    with pytest.raises(ValueError):
        Measurement(
            estimate=Exact(1.0), n=5, eligible=4, censored=1,
            coverage=1.0, label_source="x", horizon="y",
        )


def test_mean_of_unrelated_breaker_latencies_is_noestimate():
    progress = Oracle(
        stalled_from=1, stop_required_by=3, any_stop_is_false_alarm=False,
        expected=DETECTION, label_source="fixture", horizon_sessions=3,
        expected_exit=3,
    )
    crash = Oracle(
        stalled_from=1, stop_required_by=1, any_stop_is_false_alarm=False,
        expected=DETECTION, label_source="fixture", horizon_sessions=1,
        expected_exit=1,
    )
    unacc = Oracle(
        stalled_from=1, stop_required_by=5, any_stop_is_false_alarm=False,
        expected=DETECTION, label_source="fixture", horizon_sessions=5,
        expected_exit=4,
    )
    rows = (
        score_run("stall", _obs(exit_code=3, polls=3, launch_polls=(0,)), progress),
        score_run("crash", _obs(exit_code=1, polls=1, launch_polls=(0,)), crash),
        score_run("unacc", _obs(exit_code=4, polls=55, launch_polls=(0,)), unacc),
    )
    card = scorecard(rows, label_source="fixture", horizon="unit1")
    assert isinstance(card.detection_latency.estimate, NoEstimate)
    assert card.detection_latency.estimate.reason == "latencies span unrelated breakers"


def test_harness_error_is_invalid_and_moves_neither_rate():
    required = Oracle(
        stalled_from=1, stop_required_by=3, any_stop_is_false_alarm=False,
        expected=DETECTION, label_source="fixture", horizon_sessions=3,
        expected_exit=3,
    )
    control = Oracle(
        stalled_from=None, stop_required_by=None, any_stop_is_false_alarm=True,
        expected=TRUE_NEGATIVE, label_source="fixture", horizon_sessions=4,
    )
    detected = score_run("ok", _obs(exit_code=3), required)
    boom = score_run(
        "boom", _obs(exit_code=1, error="RuntimeError: harness"), required)
    quiet = score_run("quiet", _obs(exit_code=0), control)
    assert boom.outcome == "invalid"
    assert detected.outcome == DETECTION
    card = scorecard((detected, boom, quiet), label_source="fixture", horizon="unit1")
    assert card.miss_rate.n == 1
    assert card.miss_rate.eligible == 2
    assert card.miss_rate.censored == 1
    assert card.miss_rate.coverage == 0.5
    assert isinstance(card.miss_rate.estimate, Estimated)
    assert card.miss_rate.estimate.value == 0.0
    assert card.false_alarm_rate.n == 1
    assert card.false_alarm_rate.eligible == 1
    assert card.false_alarm_rate.censored == 0
    assert isinstance(card.false_alarm_rate.estimate, Estimated)
    assert card.false_alarm_rate.estimate.value == 0.0


def test_log_prose_does_not_score_a_detection():
    required = Oracle(
        stalled_from=1, stop_required_by=3, any_stop_is_false_alarm=False,
        expected=DETECTION, label_source="fixture", horizon_sessions=3,
        expected_exit=3,
    )
    row = score_run(
        "quiet",
        _obs(exit_code=2, log_text="Progress breaker tripped"),
        required,
    )
    assert row.outcome == "invalid"
    assert not kernel_stopped(
        _obs(exit_code=2, log_text="Progress breaker tripped"))


def test_latency_without_onset_is_noestimate_not_the_run_length():
    oracle = Oracle(
        stalled_from=None, stop_required_by=5, any_stop_is_false_alarm=False,
        expected=DETECTION, label_source="fixture", horizon_sessions=5,
        expected_exit=4,
    )
    row = score_run("unacc", _obs(exit_code=4, polls=55, launch_polls=(0,)), oracle)
    assert row.outcome == DETECTION
    assert row.latency.polls is None
    text = format_scorecard(scorecard((row,), label_source="fixture", horizon="unit1"))
    assert "latency 55" not in text
    assert "oracle declares no onset" in text


def test_format_scorecard_is_ascii():
    row = score_run("demo", _obs(exit_code=3), Oracle(
        stalled_from=1, stop_required_by=3, any_stop_is_false_alarm=False,
        expected=DETECTION, label_source="fixture", horizon_sessions=3,
        expected_exit=3,
    ))
    text = format_scorecard(scorecard((row,), label_source="fixture", horizon="unit1"))
    text.encode("ascii")
    assert "miss_rate" in text
    assert "false_alarm_rate" in text
    assert "verdict_in_ledger_coverage" in text
    assert "ledger_chain_verified" in text
    assert math.isfinite(row.latency.bound_polls)


def test_an_errored_run_is_invalid_even_when_its_exit_code_was_expected():
    """crash-loop expects exit 1, and the child also exits 1 on any exception,
    so the exit code alone cannot tell a kernel give-up from a harness crash."""
    oracle = Oracle(
        stalled_from=1, stop_required_by=5, any_stop_is_false_alarm=False,
        expected=DETECTION, label_source="fixture", horizon_sessions=6,
        expected_exit=1,
    )
    clean = score_run("give-up", _obs(exit_code=1), oracle)
    crashed = score_run("crash", _obs(exit_code=1, error="ImportError: boom"), oracle)
    assert clean.outcome == DETECTION
    assert crashed.outcome == "invalid"
    card = scorecard((clean, crashed), label_source="fixture", horizon="t")
    assert card.miss_rate.n == 1
    assert card.miss_rate.censored == 1


def test_an_unexpected_error_is_invalid_even_when_a_give_up_error_was_declared():
    """The kernel's launch-failure give-up re-raises MuxSessionError rather than
    returning an exit code, so an oracle may declare it. Any other error is still
    the harness falling over, and must not be scored as a detection."""
    oracle = Oracle(
        stalled_from=1, stop_required_by=1, any_stop_is_false_alarm=False,
        expected=DETECTION, label_source="fixture", horizon_sessions=1,
        expected_exit=1, expected_error="MuxSessionError",
    )
    declared = score_run(
        "gave-up", _obs(exit_code=1, error="MuxSessionError: scripted"), oracle)
    other = score_run(
        "broke", _obs(exit_code=1, error="ImportError: no module"), oracle)
    assert declared.outcome == DETECTION
    assert other.outcome == "invalid"


def test_ledger_verdict_counts_match_session_numbers_not_event_totals():
    """A spare progress_verdict must not cover a session_exit it does not name."""
    obs = _obs(
        session_exits=(
            {"event": "session_exit", "session": 1},
            {"event": "session_exit", "session": 2},
        ),
        records=(
            {"event": "session_exit", "session": 1},
            {"event": "session_exit", "session": 2},
            {"event": "progress_verdict", "session": 1},
            {"event": "progress_verdict", "session": 99},
        ),
    )
    assert ledger_verdict_counts(obs) == (2, 1)


def test_verdict_in_ledger_coverage_is_zero_when_no_verdict_was_written():
    required = Oracle(
        stalled_from=1, stop_required_by=3, any_stop_is_false_alarm=False,
        expected=DETECTION, label_source="fixture", horizon_sessions=3,
        expected_exit=3,
    )
    row = score_run(
        "silent",
        _obs(exit_code=3, session_exits=({"event": "session_exit", "session": 1},),
             records=({"event": "session_exit", "session": 1},)),
        required,
    )
    card = scorecard((row,), label_source="fixture", horizon="unit1")
    m = card.verdict_in_ledger_coverage
    assert m.n == 1
    assert m.eligible == 1
    assert m.censored == 0
    assert m.n + m.censored <= m.eligible
    assert isinstance(m.estimate, Estimated)
    assert m.estimate.value == 0
    assert m.coverage == 1.0


def test_verdict_in_ledger_coverage_is_one_when_every_exit_has_a_verdict():
    required = Oracle(
        stalled_from=1, stop_required_by=3, any_stop_is_false_alarm=False,
        expected=DETECTION, label_source="fixture", horizon_sessions=3,
        expected_exit=3,
    )
    exits = (
        {"event": "session_exit", "session": 1},
        {"event": "session_exit", "session": 2},
    )
    records = exits + (
        {"event": "progress_verdict", "session": 1},
        {"event": "progress_verdict", "session": 2},
    )
    row = score_run(
        "covered",
        _obs(exit_code=3, session_exits=exits, records=records),
        required,
    )
    card = scorecard((row,), label_source="fixture", horizon="unit1")
    m = card.verdict_in_ledger_coverage
    assert m.n == 2
    assert m.eligible == 2
    assert m.censored == 0
    assert m.n + m.censored <= m.eligible
    assert isinstance(m.estimate, Estimated)
    assert m.estimate.value == 1
    assert m.coverage == 1.0


def test_ledger_chain_verified_is_noestimate_when_the_chain_is_absent():
    required = Oracle(
        stalled_from=1, stop_required_by=3, any_stop_is_false_alarm=False,
        expected=DETECTION, label_source="fixture", horizon_sessions=3,
        expected_exit=3,
    )
    row = score_run(
        "old",
        _obs(exit_code=3, records=({"event": "session_exit", "session": 1},)),
        required,
    )
    card = scorecard((row,), label_source="fixture", horizon="unit1")
    m = card.ledger_chain_verified
    assert isinstance(m.estimate, NoEstimate)
    assert "chain" in m.estimate.reason
    assert not isinstance(m.estimate, Estimated)
    text = format_scorecard(card)
    assert "ledger_chain_verified" in text
    assert "NoEstimate" in text


def test_ledger_chain_verified_is_one_when_every_run_verifies():
    required = Oracle(
        stalled_from=1, stop_required_by=3, any_stop_is_false_alarm=False,
        expected=DETECTION, label_source="fixture", horizon_sessions=3,
        expected_exit=3,
    )
    row = score_run(
        "chained",
        _obs(exit_code=3, chain="verified"),
        required,
    )
    card = scorecard((row,), label_source="fixture", horizon="unit1")
    m = card.ledger_chain_verified
    assert isinstance(m.estimate, Estimated)
    assert m.estimate.value == 1
    assert m.n == 1
    assert m.eligible == 1
    assert m.censored == 0


def test_spend_recorded_coverage_is_noestimate_without_session_cost():
    oracle = Oracle(
        stalled_from=None, stop_required_by=None, any_stop_is_false_alarm=False,
        expected=TRUE_NEGATIVE, label_source="fixture", horizon_sessions=2,
        spend_ceiling=2.0,
    )
    row = score_run(
        "spend-ceiling",
        _obs(exit_code=2, error="ClockExhausted",
             session_exits=(
                 {"event": "session_exit", "session": 1},
                 {"event": "session_exit", "session": 2},
             ),
             records=(
                 {"event": "session_exit", "session": 1},
                 {"event": "session_exit", "session": 2},
             ),
             launch_polls=(0, 1)),
        oracle,
    )
    card = scorecard((row,), label_source="fixture", horizon="unit1")
    m = card.spend_recorded_coverage
    assert isinstance(m.estimate, NoEstimate)
    assert "session_cost" in m.estimate.reason


def test_spend_recorded_coverage_is_one_when_every_exit_has_a_cost():
    oracle = Oracle(
        stalled_from=None, stop_required_by=None, any_stop_is_false_alarm=False,
        expected=TRUE_NEGATIVE, label_source="fixture", horizon_sessions=2,
        spend_ceiling=2.0,
    )
    exits = (
        {"event": "session_exit", "session": 1},
        {"event": "session_exit", "session": 2},
    )
    row = score_run(
        "spend-ceiling",
        _obs(exit_code=2, error="ClockExhausted",
             session_exits=exits,
             records=exits + (
                 {"event": "session_cost", "session": 1, "amount": 1},
                 {"event": "session_cost", "session": 2, "amount": 2},
             ),
             launch_polls=(0, 1)),
        oracle,
    )
    card = scorecard((row,), label_source="fixture", horizon="unit1")
    m = card.spend_recorded_coverage
    assert m.estimate.value == 1
    assert m.n == 2
    assert m.eligible == 2
    assert m.n + m.censored <= m.eligible


def test_spend_ceiling_fidelity_is_zero_when_launches_pass_the_ceiling():
    oracle = Oracle(
        stalled_from=None, stop_required_by=None, any_stop_is_false_alarm=False,
        expected=TRUE_NEGATIVE, label_source="fixture", horizon_sessions=4,
        spend_ceiling=2.0,
    )
    row = score_run(
        "spend-ceiling",
        _obs(exit_code=0, launch_polls=(0, 1, 2, 3),
             session_exits=tuple(
                 {"event": "session_exit", "session": n} for n in (1, 2, 3, 4)),
             records=()),
        oracle,
    )
    assert row.ceiling_held is False
    card = scorecard((row,), label_source="fixture", horizon="unit1")
    m = card.spend_ceiling_fidelity
    assert m.estimate.value == 0
    assert m.n == 1
    assert m.eligible == 1


def test_spend_ceiling_fidelity_is_one_when_launching_stops_at_the_ceiling():
    oracle = Oracle(
        stalled_from=None, stop_required_by=None, any_stop_is_false_alarm=False,
        expected=TRUE_NEGATIVE, label_source="fixture", horizon_sessions=4,
        spend_ceiling=2.0,
    )
    exits = (
        {"event": "session_exit", "session": 1},
        {"event": "session_exit", "session": 2},
    )
    row = score_run(
        "spend-ceiling",
        _obs(exit_code=2, error="ClockExhausted", launch_polls=(0, 1),
             session_exits=exits,
             records=exits + (
                 {"event": "session_cost", "session": 1, "amount": 1},
                 {"event": "session_cost", "session": 2, "amount": 2},
             )),
        oracle,
    )
    assert row.ceiling_held is True
    card = scorecard((row,), label_source="fixture", horizon="unit1")
    m = card.spend_ceiling_fidelity
    assert m.estimate.value == 1
    assert m.n == 1
    assert m.n + m.censored <= m.eligible


def test_format_scorecard_names_the_spend_metrics():
    oracle = Oracle(
        stalled_from=None, stop_required_by=None, any_stop_is_false_alarm=False,
        expected=TRUE_NEGATIVE, label_source="fixture", horizon_sessions=2,
        spend_ceiling=2.0,
    )
    row = score_run("spend-ceiling", _obs(exit_code=0, launch_polls=(0, 1)), oracle)
    text = format_scorecard(scorecard((row,), label_source="fixture", horizon="unit1"))
    text.encode("ascii")
    assert "spend_recorded_coverage" in text
    assert "spend_ceiling_fidelity" in text
