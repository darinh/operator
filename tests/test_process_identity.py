"""parse_ts stays with the identity module after claims was removed."""
from __future__ import annotations

from datetime import datetime, timezone

from process_identity import parse_ts


def test_parse_ts_reads_the_utc_stamp_claims_used_to_own():
    parsed = parse_ts("2026-09-20T12:00:00Z")
    assert parsed == datetime(2026, 9, 20, 12, 0, 0, tzinfo=timezone.utc)


def test_parse_ts_refuses_a_stamp_it_cannot_read():
    assert parse_ts(None) is None
    assert parse_ts("not-a-stamp") is None
