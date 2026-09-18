"""An appended record occupies exactly the bytes its caller budgeted for it.

`journal.remember` refuses a write that would carry the journal past
`MAX_JOURNAL_BYTES`, and it decides by predicting the size of the record it is
about to append: the encoded JSON, plus `1` for the separator. That prediction
is only true if the separator really is one byte.

It was not. `evidence._append` opened the file in text mode, where CPython
translates `"\\n"` to `"\\r\\n"` on Windows, so every record landed one byte
larger than the caller had been told it would. An independent verifier drove
the real `operator-seat` binary to a journal of 4,194,305 bytes against a cap
of 4,194,304 -- past a limit the feature notes stated it could never exceed.

The same arithmetic is load-bearing one package over. `LedgerTail` is built
entirely on byte offsets into `trace.jsonl` and re-reads from a stored
position, so a separator whose width depends on the operating system makes the
ledger's offsets mean different things on different machines.

These tests are about *bytes on disk*, so they read bytes. Asserting through a
text-mode read would apply the same translation being tested and agree with
whatever was written.
"""
from __future__ import annotations

import json

import evidence


def budgeted_size(record: dict) -> int:
    """What `journal.remember` predicts a record will cost.

    Deliberately spelled the way `operator_memory.journal` spells it rather
    than imported, so that a change to one and not the other shows up here as
    a disagreement instead of as two names moving together.
    """
    return len(json.dumps(record).encode("utf-8")) + 1


def test_a_record_costs_what_the_caller_was_told_it_would(tmp_path):
    path = tmp_path / "journal.jsonl"
    record = {"id": "abcd1234", "kind": "gotcha", "body": "x" * 40}

    assert evidence._append(path, record) is True
    assert path.stat().st_size == budgeted_size(record)


def test_the_separator_is_one_byte_on_every_platform(tmp_path):
    """The defect exactly: `\\r\\n` here is a byte nobody accounted for."""
    path = tmp_path / "ledger.jsonl"
    evidence._append(path, {"event": "one"})
    raw = path.read_bytes()

    assert raw.endswith(b"\x0a")
    assert b"\x0d\x0a" not in raw
    assert raw.count(b"\x0a") == 1


def test_many_records_do_not_accumulate_a_drift(tmp_path):
    """One byte per record is what turns a cap into an overrun.

    A single record being right is not enough: the failure only becomes
    visible at the boundary, after thousands of writes have each added their
    own byte.
    """
    path = tmp_path / "journal.jsonl"
    records = [{"id": f"id{n:06d}", "body": "y" * 20} for n in range(200)]
    for record in records:
        assert evidence._append(path, record) is True

    assert path.stat().st_size == sum(budgeted_size(r) for r in records)


def test_a_record_carrying_real_utf8_is_still_counted_correctly(tmp_path):
    """`_append` writes `ensure_ascii=False` and the estimate does not.

    So the prediction is the longer, escaped form and the file is the shorter
    one. That direction is safe -- the caller refuses slightly early rather
    than slightly late -- but it is a real difference between the two, and a
    reader who assumes they agree exactly would be wrong.
    """
    path = tmp_path / "journal.jsonl"
    record = {"body": "a seat that remembers \u00e9\u00e8\u00ea and \u65e5\u672c\u8a9e"}

    evidence._append(path, record)

    assert path.stat().st_size <= budgeted_size(record)
    assert path.read_bytes().endswith(b"\x0a")


def test_a_record_that_cannot_be_encoded_is_refused_rather_than_raised(tmp_path):
    """The guarantee the appender is written around: it never raises."""
    path = tmp_path / "journal.jsonl"

    class Circular:
        pass

    circular: dict = {}
    circular["self"] = circular

    assert evidence._append(path, circular) is False
