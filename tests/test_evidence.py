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
import multiprocessing
from pathlib import Path

import evidence
from ledger_chain import Broken, Gap, NoChain, TruncatedTail, Verified, verify


def _write_chained_records(path_str, count, tag):
    import evidence as ev
    ev._chain_writer = None
    path = Path(path_str)
    for i in range(count):
        assert ev._append(path, {"event": "probe", "tag": tag, "i": i},
                          chain=True) is True


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


def _verdict_kwargs(**overrides):
    fields = dict(
        instance="seat-id", session=4, verdict="unchanged",
        before="abc", after="abc", accounted=True,
        nochange_streak=1, unaccounted_streak=0,
        limit_nochange=3, limit_unaccounted=5,
    )
    fields.update(overrides)
    return fields


def _verdicts(home):
    path = evidence.trace_path(home)
    if not path.exists():
        return []
    records = [json.loads(line) for line in
               path.read_text(encoding="utf-8").splitlines()]
    return [r for r in records if r.get("event") == "progress_verdict"]


def test_progress_verdict_carries_the_verdict_and_both_fingerprints(tmp_path):
    evidence.record_progress_verdict(tmp_path, **_verdict_kwargs())
    records = _verdicts(tmp_path)
    assert len(records) == 1
    rec = records[0]
    for key in ("ts", "event", "pid", "instance", "session"):
        assert key in rec
    assert rec["event"] == "progress_verdict"
    assert rec["verdict"] == "unchanged"
    assert rec["before"] == "abc"
    assert rec["after"] == "abc"
    assert rec["accounted"] is True
    assert rec["session_num"] == 4
    assert rec["session"] == 4
    assert rec["nochange_streak"] == 1
    assert rec["unaccounted_streak"] == 0
    assert rec["limit_nochange"] == 3
    assert rec["limit_unaccounted"] == 5
    assert rec["instance"] == "seat-id"


def test_progress_verdict_writes_null_when_a_fingerprint_could_not_be_read(tmp_path):
    evidence.record_progress_verdict(
        tmp_path, **_verdict_kwargs(verdict="unknown", before=None, after="xyz",
                                    accounted=False, nochange_streak=None))
    rec = _verdicts(tmp_path)[0]
    assert rec["before"] is None
    assert rec["after"] == "xyz"
    assert rec["accounted"] is False
    assert rec["nochange_streak"] is None
    assert rec["verdict"] == "unknown"


def test_recording_a_progress_verdict_never_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(evidence, "_append",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("nope")))
    evidence.record_progress_verdict(tmp_path, **_verdict_kwargs())
    assert not (tmp_path / "trace.jsonl").exists()


def test_two_concurrent_writers_interleaving_both_verify(tmp_path):
    ctx = multiprocessing.get_context("spawn")
    path_a = tmp_path / "a.jsonl"
    path_b = tmp_path / "b.jsonl"
    a = ctx.Process(target=_write_chained_records, args=(str(path_a), 40, "a"))
    b = ctx.Process(target=_write_chained_records, args=(str(path_b), 40, "b"))
    a.start()
    b.start()
    a.join(30)
    b.join(30)
    assert a.exitcode == 0
    assert b.exitcode == 0
    lines_a = path_a.read_bytes().splitlines(True)
    lines_b = path_b.read_bytes().splitlines(True)
    assert len(lines_a) == 40
    assert len(lines_b) == 40
    path = tmp_path / "trace.jsonl"
    mixed = []
    for left, right in zip(lines_a, lines_b):
        mixed.append(left)
        mixed.append(right)
    path.write_bytes(b"".join(mixed))
    result = verify([path])
    assert isinstance(result, Verified)
    assert result.writers == 2
    assert result.records == 80


def test_ledger_recorders_write_a_chain_field(tmp_path):
    evidence._chain_writer = None
    evidence.record_supervisor_start(tmp_path, instance="seat", session=1)
    path = evidence.trace_path(tmp_path)
    rec = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    assert rec["event"] == "supervisor_start"
    chain = rec["chain"]
    assert chain["n"] == 1
    assert chain["p"] is None
    assert chain["d"]
    assert chain["w"]
    result = verify([path])
    assert isinstance(result, Verified)
    assert result.writers == 1
    assert result.records == 1


def test_append_without_the_opt_in_writes_no_chain_field(tmp_path):
    path = tmp_path / "journal.jsonl"
    assert evidence._append(path, {"kind": "gotcha", "body": "x"}) is True
    rec = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    assert "chain" not in rec


def test_editing_a_written_payload_reports_broken(tmp_path):
    evidence._chain_writer = None
    evidence.record_supervisor_start(tmp_path, instance="seat", session=1)
    path = evidence.trace_path(tmp_path)
    rec = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    rec["instance"] = "tampered"
    path.write_text(json.dumps(rec, ensure_ascii=False) + "\n", encoding="utf-8")
    result = verify([path])
    assert isinstance(result, Broken)
    assert result.seq == 1


def test_deleting_a_written_record_reports_gap_or_broken_for_that_writer(tmp_path):
    evidence._chain_writer = None
    evidence.record_supervisor_start(tmp_path, instance="seat", session=1)
    evidence.record_progress_verdict(tmp_path, **_verdict_kwargs(session=1))
    evidence.record_session_exit(
        tmp_path, instance="seat", session=1, pid=None,
        markers={}, consecutive=0, limit=5)
    path = evidence.trace_path(tmp_path)
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 3
    writer = json.loads(lines[0])["chain"]["w"]
    path.write_text(lines[0] + "\n" + lines[2] + "\n", encoding="utf-8")
    result = verify([path])
    assert isinstance(result, (Gap, Broken))
    assert result.writer == writer


def test_a_torn_final_line_on_disk_reports_truncated_tail(tmp_path):
    evidence._chain_writer = None
    evidence.record_supervisor_start(tmp_path, instance="seat", session=1)
    path = evidence.trace_path(tmp_path)
    path.write_bytes(path.read_bytes() + b'{"event":"partial"')
    result = verify([path])
    assert isinstance(result, TruncatedTail)
    assert not isinstance(result, Broken)


def test_a_pre_chain_ledger_file_reports_no_chain(tmp_path):
    path = evidence.trace_path(tmp_path)
    path.write_text(
        json.dumps({"event": "supervisor_start", "pid": 1}) + "\n",
        encoding="utf-8")
    result = verify([path])
    assert isinstance(result, NoChain)
    assert result.records == 1
    assert not isinstance(result, Broken)


def test_a_ledger_that_rotates_still_verifies(tmp_path, monkeypatch):
    evidence._chain_writer = None
    monkeypatch.setattr(evidence, "_MAX_BYTES", 1)
    evidence.record_supervisor_start(tmp_path, instance="seat", session=1)
    evidence.record_progress_verdict(tmp_path, **_verdict_kwargs(session=1))
    path = evidence.trace_path(tmp_path)
    rotated = path.with_suffix(path.suffix + ".1")
    assert rotated.exists()
    result = verify([rotated, path])
    assert isinstance(result, Verified)
    assert result.records == 2
    assert result.writers == 1


def test_ledger_tail_reads_progress_verdict_across_a_rotation(tmp_path, monkeypatch):
    import ledger_tail

    monkeypatch.setattr(evidence, "_MAX_BYTES", 1)
    path = evidence.trace_path(tmp_path)
    tail = ledger_tail.LedgerTail(path, tmp_path / "tail.json")
    evidence.record_progress_verdict(tmp_path, **_verdict_kwargs(session=1))
    first = tail.read()
    assert [r.get("event") for r in first] == ["progress_verdict"]
    assert first[0]["session"] == 1
    assert "chain" in first[0]
    evidence.record_progress_verdict(tmp_path, **_verdict_kwargs(session=2))
    assert path.with_suffix(path.suffix + ".1").exists()
    second = tail.read()
    assert [r.get("event") for r in second] == ["progress_verdict"]
    assert second[0]["session"] == 2
    assert second[0]["verdict"] == "unchanged"
    assert "chain" in second[0]
