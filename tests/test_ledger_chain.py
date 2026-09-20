"""The ledger chain is per writer. These tests are pure functions over records."""
from __future__ import annotations

from ledger_chain import (
    Broken, Gap, NoChain, TruncatedTail, Verified, Writer, digest,
    verify_records,
)


def test_a_recomputed_digest_matches_what_was_stored():
    payload = {"event": "probe", "k": "v"}
    d = digest(None, payload)
    w = Writer("w1")
    rec = w.stamp(payload)
    assert rec["chain"]["d"] == d
    assert rec["chain"]["p"] is None
    assert rec["chain"]["n"] == 1
    assert rec["chain"]["w"] == "w1"


def test_editing_a_payload_reports_broken_at_that_record():
    w = Writer("alice")
    rec = w.stamp({"event": "probe", "body": "ok"})
    rec["body"] = "tampered"
    result = verify_records([rec])
    assert isinstance(result, Broken)
    assert result.writer == "alice"
    assert result.seq == 1
    assert result.reason == "digest"


def test_deleting_a_middle_record_reports_gap_for_that_writer():
    w = Writer("alice")
    first = w.stamp({"event": "one"})
    w.stamp({"event": "two"})
    third = w.stamp({"event": "three"})
    result = verify_records([first, third])
    assert isinstance(result, Gap)
    assert result.writer == "alice"
    assert result.after_seq == 1
    assert result.before_seq == 3


def test_a_torn_final_line_reports_truncated_tail_not_broken():
    w = Writer("alice")
    rec = w.stamp({"event": "probe"})
    result = verify_records([rec], truncated_bytes=17)
    assert isinstance(result, TruncatedTail)
    assert result.bytes_dropped == 17
    assert not isinstance(result, Broken)


def test_a_pre_chain_ledger_reports_no_chain():
    records = [
        {"event": "supervisor_start", "pid": 1},
        {"event": "session_exit", "pid": 1},
    ]
    result = verify_records(records)
    assert isinstance(result, NoChain)
    assert result.records == 2
    assert not isinstance(result, Broken)
    assert not isinstance(result, Gap)


def test_an_unchained_prefix_then_a_chain_verifies():
    w = Writer("alice")
    records = [
        {"event": "supervisor_start", "pid": 1},
        w.stamp({"event": "session_exit", "pid": 1}),
    ]
    result = verify_records(records)
    assert isinstance(result, Verified)
    assert result.writers == 1
    assert result.records == 1


def test_two_writers_interleaved_both_verify():
    a = Writer("a")
    b = Writer("b")
    records = [
        a.stamp({"event": "a", "i": 1}),
        b.stamp({"event": "b", "i": 1}),
        a.stamp({"event": "a", "i": 2}),
        b.stamp({"event": "b", "i": 2}),
    ]
    result = verify_records(records)
    assert isinstance(result, Verified)
    assert result.writers == 2
    assert result.records == 4


def test_a_prev_digest_mismatch_reports_broken():
    w = Writer("alice")
    first = w.stamp({"event": "one"})
    second = w.stamp({"event": "two"})
    second["chain"] = dict(second["chain"], p="0" * 64)
    result = verify_records([first, second])
    assert isinstance(result, Broken)
    assert result.writer == "alice"
    assert result.seq == 2
    assert result.reason == "digest"


def test_a_writer_whose_first_seen_seq_is_not_one_is_a_gap():
    w = Writer("alice")
    w.stamp({"event": "one"})
    second = w.stamp({"event": "two"})
    result = verify_records([second])
    assert isinstance(result, Gap)
    assert result.writer == "alice"
    assert result.after_seq == 0
    assert result.before_seq == 2
