"""Durable operator records.

A clean stop deletes the live state files. The record is what remains, so a
stopped operator can still be listed, started, renamed and deleted. The id
never changes. The name does.
"""
from __future__ import annotations

import json
import os
import secrets
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from instance import Instance
from probes import utcnow


class BadName(ValueError):
    """A display name the records will not store."""


@dataclass(frozen=True)
class Operator:
    id: str
    name: str
    cwd: str
    created: str

    def instance(self) -> Instance:
        return Instance(self.id, self.name)


def records_dir() -> Path:
    import config
    return config.OPERATOR_HOME / "operators"


def _path(op_id: str) -> Path:
    return records_dir() / f"{op_id}.json"


def _load(path: Path) -> Operator | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    op_id = payload.get("id")
    name = payload.get("name")
    cwd = payload.get("cwd")
    created = payload.get("created")
    if not all(isinstance(value, str) and value
               for value in (op_id, name, cwd, created)):
        return None
    if path.stem != op_id or name != name.strip():
        return None
    return Operator(op_id, name, cwd, created)


def _write(op: Operator) -> None:
    directory = records_dir()
    directory.mkdir(parents=True, exist_ok=True)
    dest = _path(op.id)
    tmp = dest.with_name(f"{dest.name}.{os.getpid()}.tmp")
    payload = {"id": op.id, "name": op.name, "cwd": op.cwd, "created": op.created}
    tmp.write_text(json.dumps(payload), encoding="utf-8")
    os.replace(tmp, dest)


def unreadable() -> list[Path] | None:
    """Record files that could not be loaded, or None if the directory could not."""
    directory = records_dir()
    if not directory.exists():
        return []
    try:
        entries = list(directory.iterdir())
    except OSError:
        return None
    bad = [path for path in entries if path.suffix == ".json" and _load(path) is None]
    return sorted(bad)


def all_operators() -> list[Operator] | None:
    directory = records_dir()
    if not directory.exists():
        return []
    try:
        entries = list(directory.iterdir())
    except OSError:
        return None
    found = []
    for path in entries:
        if path.suffix != ".json":
            continue
        op = _load(path)
        if op is not None:
            found.append(op)
    return sorted(found, key=lambda op: op.name.casefold())


def name_problem(name: str, *, ignore_id: str = "") -> str | None:
    cleaned = name.strip()
    if not cleaned:
        return "a name is needed"
    if len(cleaned) > 64:
        return "a name can be at most 64 characters"
    if cleaned.startswith("-"):
        return "a name cannot start with -"
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in cleaned):
        return "a name cannot contain control characters"
    others = all_operators()
    if others is None:
        return "could not read operators"
    folded = cleaned.casefold()
    for op in others:
        if op.id == ignore_id:
            continue
        if op.name.casefold() == folded:
            return f"an operator named {cleaned!r} already exists"
        if op.id.casefold() == folded:
            return f"{cleaned!r} is another operator's id"
    return None


def _new_id() -> str:
    directory = records_dir()
    for _ in range(8):
        op_id = "op-" + secrets.token_hex(4)
        if not (directory / f"{op_id}.json").exists():
            return op_id
    raise BadName("could not allocate an operator id")


_LOCK_WAIT = 0.25
_LOCK_STALE = 10.0


@contextmanager
def _records_lock():
    directory = records_dir()
    directory.mkdir(parents=True, exist_ok=True)
    lock = directory / ".lock"
    deadline = time.monotonic() + _LOCK_WAIT
    fd = None
    while fd is None:
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_RDWR)
        except FileExistsError:
            try:
                # Held far longer than any create or rename takes: its owner died.
                if time.time() - lock.stat().st_mtime > _LOCK_STALE:
                    lock.unlink()
                    continue
            except OSError:
                pass
            if time.monotonic() >= deadline:
                raise BadName("could not lock operator records")
            time.sleep(0.02)
    try:
        yield
    finally:
        os.close(fd)
        try:
            lock.unlink()
        except OSError:
            pass


def create(name: str, cwd: Path) -> Operator:
    with _records_lock():
        problem = name_problem(name)
        if problem:
            raise BadName(problem)
        op = Operator(_new_id(), name.strip(), str(Path(cwd).resolve()), utcnow())
        _write(op)
        return op


def find(name_or_id: str) -> Operator | None:
    found = all_operators()
    if not found:
        return None
    wanted = name_or_id.strip()
    for op in found:
        if op.id == wanted:
            return op
    for op in found:
        if op.name.casefold() == wanted.casefold():
            return op
    return None


def rename(op: Operator, new_name: str) -> Operator:
    with _records_lock():
        problem = name_problem(new_name, ignore_id=op.id)
        if problem:
            raise BadName(problem)
        updated = Operator(op.id, new_name.strip(), op.cwd, op.created)
        _write(updated)
        return updated


def remove(op: Operator) -> None:
    try:
        _path(op.id).unlink()
    except FileNotFoundError:
        return
