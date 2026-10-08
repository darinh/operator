"""Who started whom.

Pure functions over operator records, with no I/O. A record names its parent
by operator id. A parent with no record reads as the person, so deleting a
parent makes its children top level without rewriting them.
"""
from __future__ import annotations

from operators import HUMAN


def parents(records) -> dict:
    """Each record's id mapped to its parent's id, or HUMAN."""
    ids = {op.id for op in records}
    return {op.id: op.parent if op.parent in ids and op.parent != op.id else HUMAN
            for op in records}


def children(op_id: str, records) -> list:
    up = parents(records)
    return [op for op in records if up[op.id] == op_id]


def subtree(op_id: str, records) -> list:
    """Every descendant of ``op_id``, each parent before its children."""
    return [op for op, _ in _walk(op_id, records, parents(records), 0, {op_id})]


def depth(op_id: str, records) -> int:
    """1 for an operator a person started, 2 for its child, and so on."""
    up, level, seen = parents(records), 0, set()
    while op_id != HUMAN and op_id not in seen:
        seen.add(op_id)
        level += 1
        op_id = up.get(op_id, HUMAN)
    return level


def may_manage(actor: str, target_id: str, records) -> bool:
    """A person may act on any operator. An operator only on its own children."""
    return actor == HUMAN or parents(records).get(target_id) == actor


def tree(records) -> list:
    """(record, depth) for every record, each child after its parent.

    Records that a loop in hand-edited files cuts off from the top come last,
    at depth 1, so nothing a person might look for is left out.
    """
    up, seen = parents(records), set()
    rows = _walk(HUMAN, records, up, 0, seen)
    for op in records:
        if op.id not in seen:
            seen.add(op.id)
            rows += [(op, 1), *_walk(op.id, records, up, 1, seen)]
    return rows


def _walk(op_id, records, up, level, seen) -> list:
    rows = []
    for op in records:
        if up[op.id] == op_id and op.id not in seen:
            seen.add(op.id)
            rows += [(op, level + 1), *_walk(op.id, records, up, level + 1, seen)]
    return rows
