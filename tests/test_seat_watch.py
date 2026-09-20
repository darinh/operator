"""`seat_watch`, and the fact that its memory is a file.

The interesting property here is not the arithmetic, it is *where the
arithmetic lives*. `extensions.Host` spawns one process per call, so an
`on_fact` batch cannot see the batch before it in memory. Every test in this
file therefore calls the hooks as separate invocations against a shared state
file, which is what actually happens in production -- an in-process fixture
that carried a module global between calls would pass while proving nothing.
"""
from __future__ import annotations

import json

import pytest

from operator_extensions import activation, seat_watch


@pytest.fixture
def home(tmp_path, monkeypatch):
    where = tmp_path / "operator-home"
    where.mkdir()
    monkeypatch.setenv("COPILOT_OPERATOR_HOME", str(where))
    assert activation.operator_home() == where
    return where


def turn_on(home, **settings):
    (home / activation.CONFIG_NAME).write_text(
        json.dumps({seat_watch.NAME: {"enabled": True, **settings}}),
        encoding="utf-8")


def exited(instance="alpha", consecutive=1, giving_up=False,
           ts="2026-08-17T10:00:00Z", event=seat_watch.EVENT):
    return {"ts": ts, "event": event, "instance": instance,
            "consecutive": consecutive, "giving_up": giving_up}


def test_seat_watch_is_silent_until_a_human_enables_it(home):
    assert seat_watch.on_fact(facts=[exited(consecutive=9)]) is None
    assert seat_watch.propose_work() is None
    assert activation.read_state(seat_watch.NAME) == {}


def test_what_on_fact_learns_survives_into_a_later_call(home):
    """The two hooks are separate processes in production, so this is the
    whole mechanism: a file, or nothing."""
    turn_on(home)
    seat_watch.on_fact(facts=[exited(consecutive=4)])
    stored = activation.read_state(seat_watch.NAME)
    assert stored["seats"]["alpha"]["consecutive"] == 4
    assert activation.state_path(seat_watch.NAME).exists(), (
        "the state has to be on disk, because one process per call means "
        "there is nowhere else it could be")


def test_seat_watch_still_reads_a_session_exit_that_carries_a_chain(home):
    turn_on(home)
    record = exited(consecutive=4)
    record["chain"] = {"w": "w1", "n": 1, "p": None, "d": "abc"}
    seat_watch.on_fact(facts=[record])
    assert activation.read_state(seat_watch.NAME)["seats"]["alpha"][
        "consecutive"] == 4


def test_a_redelivered_record_does_not_inflate_the_count(home):
    """Delivery is at-least-once. A counter that incremented would report
    seats failing that never did."""
    turn_on(home, failures=3)
    record = exited(consecutive=2)
    seat_watch.on_fact(facts=[record])
    seat_watch.on_fact(facts=[record])
    seat_watch.on_fact(facts=[record])
    assert activation.read_state(seat_watch.NAME)["seats"]["alpha"][
        "consecutive"] == 2
    assert seat_watch.propose_work() is None, "2 is below the threshold of 3"


def test_only_session_exit_records_are_counted(home):
    turn_on(home)
    seat_watch.on_fact(facts=[
        exited(event="supervisor_start", consecutive=9),
        exited(event="launch_admission", consecutive=9),
        exited(event="progress_verdict", consecutive=9),
    ])
    assert activation.read_state(seat_watch.NAME) == {}


def test_progress_verdict_is_ignored_even_when_it_looks_like_a_failure(home):
    turn_on(home, failures=1)
    seat_watch.on_fact(facts=[{
        "ts": "2026-08-17T10:00:00Z",
        "event": "progress_verdict",
        "instance": "alpha",
        "session": 3,
        "verdict": "unchanged",
        "consecutive": 9,
        "giving_up": True,
        "nochange_streak": 9,
    }])
    assert activation.read_state(seat_watch.NAME) == {}
    assert seat_watch.propose_work() is None


@pytest.mark.parametrize("record", [
    "not a dict", 42, None, {},
    {"event": "session_exit"},
    {"event": "session_exit", "instance": ""},
    {"event": "session_exit", "instance": "a"},
    {"event": "session_exit", "instance": "a", "consecutive": "3"},
    {"event": "session_exit", "instance": "a", "consecutive": True},
])
def test_a_malformed_record_is_skipped_rather_than_raising(home, record):
    """A traceback out of `on_fact` is a Failure, and a Failure on every batch
    is an extension that has stopped observing."""
    turn_on(home)
    assert seat_watch.on_fact(facts=[record]) is None
    assert activation.read_state(seat_watch.NAME).get("seats", {}) == {}


def test_a_seat_over_the_threshold_is_proposed_exactly_once(home):
    turn_on(home, failures=3)
    seat_watch.on_fact(facts=[exited(consecutive=3)])

    first = seat_watch.propose_work()
    assert first and "alpha" in first[0]["title"]
    assert "3 sessions" in first[0]["title"]
    assert seat_watch.propose_work() is None


def test_a_seat_that_recovers_and_fails_again_is_reported_again(home):
    """Otherwise the module does exactly what its docstring says it must not.

    The supervisor writes `consecutive=0` on a healthy ending, so a seat that
    recovers and then fails to the same depth as before was silently suppressed
    by the remembered `told` -- reportable only if the new streak happened to
    be worse.
    """
    turn_on(home, failures=3)
    seat_watch.on_fact(facts=[exited(consecutive=3)])
    assert seat_watch.propose_work()

    seat_watch.on_fact(facts=[exited(consecutive=0)])
    assert seat_watch.propose_work() is None

    seat_watch.on_fact(facts=[exited(consecutive=3)])
    again = seat_watch.propose_work()
    assert again, "a recovered seat that fails again must be reported again"
    assert "3 sessions" in again[0]["title"]


def test_a_seat_that_gets_worse_is_proposed_again(home):
    turn_on(home, failures=3)
    seat_watch.on_fact(facts=[exited(consecutive=3)])
    assert seat_watch.propose_work()

    seat_watch.on_fact(facts=[exited(consecutive=5)])
    worse = seat_watch.propose_work()
    assert worse and "5 sessions" in worse[0]["title"]


def test_a_seat_that_has_given_up_says_so(home):
    turn_on(home, failures=1)
    seat_watch.on_fact(facts=[exited(consecutive=5, giving_up=True)])
    proposals = seat_watch.propose_work()
    assert "has stopped" in proposals[0]["title"]
    assert "no longer running" in proposals[0]["detail"]


def test_the_threshold_is_configurable_and_bad_values_fall_back(home):
    turn_on(home, failures=0)
    seat_watch.on_fact(facts=[exited(consecutive=2)])
    assert seat_watch.propose_work() is None, (
        "a threshold of 0 is nonsense and must not mean 'report everything'")


def test_no_more_than_a_handful_of_seats_are_reported_in_one_call(home):
    turn_on(home, failures=1)
    seat_watch.on_fact(facts=[exited(instance=f"seat{n}", consecutive=2)
                              for n in range(12)])
    proposals = seat_watch.propose_work()
    assert len(proposals) == seat_watch.MAX_PER_CALL


def test_on_tick_forgets_a_seat_nothing_has_mentioned_for_a_week(home):
    turn_on(home, forget_after_days=7)
    seat_watch.on_fact(facts=[exited(instance="old", ts="2026-08-01T00:00:00Z"),
                              exited(instance="new", ts="2026-08-17T00:00:00Z")])
    seat_watch.on_tick(now="2026-08-17T12:00:00Z", elapsed=300.0)
    seats = activation.read_state(seat_watch.NAME)["seats"]
    assert "old" not in seats and "new" in seats


def test_on_tick_keeps_a_seat_whose_timestamp_will_not_parse(home):
    """Losing the record of a failing seat because a clock format changed is
    the worse of the two available errors."""
    turn_on(home)
    seat_watch.on_fact(facts=[exited(instance="odd", ts="last tuesday")])
    seat_watch.on_tick(now="2030-01-01T00:00:00Z", elapsed=1.0)
    assert "odd" in activation.read_state(seat_watch.NAME)["seats"]


def test_on_tick_is_silent_when_nothing_is_enabled(home):
    assert seat_watch.on_tick(now="2026-08-17T12:00:00Z", elapsed=1.0) is None


@pytest.mark.parametrize("value", [
    "2026-08-17T10:00:00Z", "2026-08-17T10:00:00+00:00",
])
def test_the_timestamp_parser_accepts_what_the_ledger_writes(value):
    parsed = seat_watch._parse_ts(value)
    assert parsed is not None and parsed.tzinfo is not None


@pytest.mark.parametrize("value", ["", None, "nonsense", 42, "2026-13-45"])
def test_the_timestamp_parser_refuses_the_rest(value):
    assert seat_watch._parse_ts(value) is None


# ── activation, which every hook above depends on ───────────────

def test_activation_needs_enabled_to_be_exactly_true(home):
    for value in ("true", 1, "yes", None, [], {}):
        (home / activation.CONFIG_NAME).write_text(
            json.dumps({seat_watch.NAME: {"enabled": value}}),
            encoding="utf-8")
        assert activation.settings(seat_watch.NAME) is None, (
            f"{value!r} must not open the launch path")


def test_a_broken_config_file_enables_nothing(home):
    (home / activation.CONFIG_NAME).write_text("{not json", encoding="utf-8")
    assert activation.settings(seat_watch.NAME) is None


def test_a_config_that_is_not_an_object_enables_nothing(home):
    (home / activation.CONFIG_NAME).write_text("[1, 2, 3]", encoding="utf-8")
    assert activation.settings(seat_watch.NAME) is None


def test_a_missing_config_file_enables_nothing(home):
    assert not activation.config_path().exists()
    assert activation.settings(seat_watch.NAME) is None


def test_state_is_replaced_atomically_and_leaves_no_temporary_behind(home):
    assert activation.write_state("probe", {"a": 1}) is True
    assert activation.read_state("probe") == {"a": 1}
    assert activation.write_state("probe", {"b": 2}) is True
    assert activation.read_state("probe") == {"b": 2}
    leftovers = list(activation.state_path("probe").parent.glob("*.tmp"))
    assert leftovers == []


def test_unwritable_state_is_reported_rather_than_raising(home, monkeypatch):
    monkeypatch.setattr(activation.os, "replace",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("no")))
    assert activation.write_state("probe", {"a": 1}) is False
    assert list(activation.state_path("probe").parent.glob("*.tmp")) == []


def test_read_state_turns_a_corrupt_file_into_an_empty_dict(home):
    path = activation.state_path("probe")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{half a doc", encoding="utf-8")
    assert activation.read_state("probe") == {}
