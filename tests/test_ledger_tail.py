"""`LedgerTail.snapshot`: the whole of one file, read once.

`read` is the poller's entry point and follows a rotation by design. These
tests are about the other reader -- the one-shot kind, which wants the bytes
that were on disk when it opened the file and must not chase a successor.

The rotation case is the reason this file exists. `operator trace` drained the
already-rotated `trace.jsonl.1` through `read`, which re-opens by name for each
batch. The writer renames the live file *onto* `trace.jsonl.1`, so a second
rotation mid-drain changed that file's identity, `read` went looking for
`trace.jsonl.1.1`, found nothing, and abandoned everything it had not reached.
"""
from __future__ import annotations

import json
from pathlib import Path

import ledger_tail


def _write(path: Path, count: int, start: int = 0) -> None:
    path.write_text(
        "".join(json.dumps({"n": start + i}) + "\n" for i in range(count)),
        encoding="utf-8")


def test_snapshot_reads_a_file_larger_than_one_batch(tmp_path):
    path = tmp_path / "trace.jsonl"
    _write(path, ledger_tail.MAX_FACTS_PER_POLL * 2 + 7)
    records = ledger_tail.LedgerTail(path, state=None).snapshot()
    assert [r["n"] for r in records] == list(range(len(records)))
    assert len(records) == ledger_tail.MAX_FACTS_PER_POLL * 2 + 7


def test_snapshot_keeps_reading_the_file_it_opened_when_the_name_is_taken(
        tmp_path):
    """The measured defect: a rotation mid-drain cost 700 of 1204 records.

    The drain is long enough to need three batches. Between them the name is
    taken over by a different file, exactly as `evidence._rotate_if_needed`
    does it.

    The two platforms protect this differently and the test asserts whichever
    one it got. On POSIX the rename succeeds and the held descriptor keeps
    pointing at the original inode. On Windows the open handle makes the
    rename fail outright, which `_open_identified` already documents. Either
    way the record count is the invariant, and asserting the mechanism as well
    stops this passing vacuously on the platform that never renamed.
    """
    path = tmp_path / "trace.jsonl.1"
    expected = ledger_tail.MAX_FACTS_PER_POLL * 2 + 50
    _write(path, expected)

    tail = ledger_tail.LedgerTail(path, state=None)
    real_read_handle = tail._read_handle
    calls: list[int] = []
    renamed: list[bool] = []

    def steal_the_name(handle, offset, limit):
        out = real_read_handle(handle, offset, limit)
        calls.append(offset)
        if len(calls) == 1:
            usurper = tmp_path / "other.jsonl"
            _write(usurper, 3, start=9000)
            try:
                usurper.replace(path)
                renamed.append(True)
            except PermissionError:
                renamed.append(False)
        return out

    tail._read_handle = steal_the_name
    records = tail.snapshot()

    assert len(calls) > 2, "the drain must span more than one batch"
    assert renamed == [True] or renamed == [False]
    assert len(records) == expected
    assert [r["n"] for r in records] == list(range(expected))


def test_snapshot_of_a_missing_file_is_empty(tmp_path):
    tail = ledger_tail.LedgerTail(tmp_path / "trace.jsonl", state=None)
    assert tail.snapshot() == []


def test_snapshot_of_an_empty_file_is_empty(tmp_path):
    path = tmp_path / "trace.jsonl"
    path.write_text("", encoding="utf-8")
    assert ledger_tail.LedgerTail(path, state=None).snapshot() == []


def test_snapshot_counts_a_line_it_cannot_read_instead_of_passing_over_it(
        tmp_path):
    path = tmp_path / "trace.jsonl"
    path.write_text(
        json.dumps({"n": 1}) + "\n"
        + "{not json\n"
        + json.dumps({"n": 2}) + "\n",
        encoding="utf-8")
    tail = ledger_tail.LedgerTail(path, state=None)
    records = tail.snapshot()
    assert [r["n"] for r in records] == [1, 2]
    assert tail.unreadable == 1


def test_snapshot_refuses_a_record_carrying_nan(tmp_path):
    """`_parse`'s rule, reached through the one-shot reader as well."""
    path = tmp_path / "trace.jsonl"
    path.write_text('{"n": NaN}\n' + json.dumps({"n": 2}) + "\n",
                    encoding="utf-8")
    tail = ledger_tail.LedgerTail(path, state=None)
    assert [r["n"] for r in tail.snapshot()] == [2]
    assert tail.unreadable == 1


def test_snapshot_drops_a_torn_final_line(tmp_path):
    path = tmp_path / "trace.jsonl"
    path.write_text(json.dumps({"n": 1}) + "\n" + '{"n": 2',
                    encoding="utf-8")
    assert [r["n"] for r in
            ledger_tail.LedgerTail(path, state=None).snapshot()] == [1]


def test_snapshot_does_not_move_the_polling_cursor(tmp_path):
    """A one-shot read must not look like a poll to whatever polls next."""
    path = tmp_path / "trace.jsonl"
    _write(path, 4)
    tail = ledger_tail.LedgerTail(path, state=None)
    before = tail.position()
    tail.snapshot()
    assert tail.position()[0] == before[0]
    assert len(tail.read()) == 4
