"""Read published artifacts after the child exits. Ledger via LedgerTail."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Observation:
    exit_code: int
    error: str | None
    records: tuple[dict, ...]
    session_exits: tuple[dict, ...]
    polls: int
    launch_polls: tuple[int, ...]
    virtual_seconds: float
    sleeps: int
    log_text: str
    chain: str = "no_chain"


def observe(home: Path) -> Observation:
    from operator_fleet.ledger_tail import LedgerTail
    from operator_kernel.ledger_chain import NoChain, Verified, verify

    home = Path(home)
    raw = json.loads((home / "bench-result.json").read_text(encoding="utf-8"))
    records = _ledger_records(LedgerTail, home)
    exits = tuple(r for r in records if r.get("event") == "session_exit")
    log_path = home / "operator.log"
    log_text = log_path.read_text(encoding="utf-8") if log_path.exists() else ""
    paths = [p for p in (home / "trace.jsonl.1", home / "trace.jsonl") if p.exists()]
    result = verify(paths) if paths else NoChain(0)
    chain = "verified" if isinstance(result, Verified) else "no_chain" if isinstance(result, NoChain) else "failed"
    return Observation(
        exit_code=int(raw.get("exit_code", 1)),
        error=raw.get("error"),
        records=records,
        session_exits=exits,
        polls=int(raw.get("polls", 0)),
        launch_polls=tuple(int(x) for x in raw.get("launch_polls", ())),
        virtual_seconds=float(raw.get("virtual_seconds", 0)),
        sleeps=int(raw.get("sleeps", 0)),
        log_text=log_text,
        chain=chain,
    )


def _ledger_records(tail_cls, home: Path) -> tuple[dict, ...]:
    path = home / "trace.jsonl"
    if not path.exists():
        return ()
    tail = tail_cls(path, home / "bench-tail.json")
    found: list[dict] = []
    while True:
        batch = tail.read()
        if not batch:
            break
        found.extend(batch)
    tail.remember()
    return tuple(found)
