"""Per-writer checksum chain for the evidence ledger.

Every supervisor process appends to the same trace.jsonl without a lock, so a
single global sequence would race. Each writer keeps its own chain. A verifier
groups by writer before checking continuity.

This is a checksum, not tamper-evidence. Re-chaining a file the writer can
overwrite takes seconds.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path


def digest(prev: "str | None", payload: dict) -> str:
    body = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    material = (prev or "") + body
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


class Writer:
    """Process-local sequence. One instance per process run, not per file."""

    def __init__(self, writer_id: str) -> None:
        self.w = writer_id
        self.n = 0
        self.p: "str | None" = None

    def stamp(self, payload: dict) -> dict:
        body = json.loads(json.dumps(payload, ensure_ascii=False, default=str))
        body.pop("chain", None)
        self.n += 1
        d = digest(self.p, body)
        rec = dict(body)
        rec["chain"] = {"w": self.w, "n": self.n, "p": self.p, "d": d}
        self.p = d
        return rec


@dataclass(frozen=True)
class Verified:
    writers: int
    records: int


@dataclass(frozen=True)
class Gap:
    writer: str
    after_seq: int
    before_seq: int


@dataclass(frozen=True)
class Broken:
    writer: str
    seq: int
    reason: str


@dataclass(frozen=True)
class TruncatedTail:
    bytes_dropped: int


@dataclass(frozen=True)
class NoChain:
    records: int


Result = Verified | Gap | Broken | TruncatedTail | NoChain


def verify_records(records, truncated_bytes: int = 0) -> Result:
    chained = []
    unchained = 0
    for rec in records:
        if not isinstance(rec, dict) or "chain" not in rec:
            unchained += 1
            continue
        chained.append(rec)
    if not chained:
        if truncated_bytes:
            return TruncatedTail(truncated_bytes)
        return NoChain(unchained)
    last: dict[str, tuple[int, str]] = {}
    for rec in chained:
        chain = rec["chain"]
        if not isinstance(chain, dict):
            return Broken("", 0, "chain")
        w = chain.get("w")
        n = chain.get("n")
        p = chain.get("p")
        d = chain.get("d")
        writer = "" if w is None else str(w)
        seq = n if isinstance(n, int) and not isinstance(n, bool) else 0
        payload = {k: v for k, v in rec.items() if k != "chain"}
        if d != digest(p if isinstance(p, str) or p is None else None, payload):
            return Broken(writer, seq, "digest")
        if not isinstance(n, int) or isinstance(n, bool):
            return Broken(writer, seq, "seq")
        if writer not in last:
            if n != 1:
                return Gap(writer, 0, n)
            if p is not None:
                return Broken(writer, n, "prev")
        else:
            prev_n, prev_d = last[writer]
            if n != prev_n + 1:
                return Gap(writer, prev_n, n)
            if p != prev_d:
                return Broken(writer, n, "prev")
        last[writer] = (n, d)
    if truncated_bytes:
        return TruncatedTail(truncated_bytes)
    return Verified(writers=len(last), records=len(chained))


def _file_records(raw: bytes, torn_ok: bool):
    if not raw:
        return [], 0, None
    chunks = raw.split(b"\n")
    if raw.endswith(b"\n"):
        chunks = chunks[:-1]
        dropped = 0
    else:
        dropped = len(chunks[-1])
        chunks = chunks[:-1]
        if not torn_ok:
            return [], dropped, Broken("", 0, "framing")
    records = []
    for chunk in chunks:
        if not chunk:
            continue
        try:
            rec = json.loads(chunk.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
            return [], 0, Broken("", 0, "json")
        records.append(rec)
    return records, dropped, None


def verify(paths) -> Result:
    paths = [Path(p) for p in paths]
    records = []
    truncated = 0
    last_index = len(paths) - 1
    for i, path in enumerate(paths):
        if not path.exists():
            continue
        recs, dropped, err = _file_records(path.read_bytes(), torn_ok=(i == last_index))
        if err is not None:
            return err
        records.extend(recs)
        if i == last_index:
            truncated = dropped
    return verify_records(records, truncated_bytes=truncated)
