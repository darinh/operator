"""Who started whom, read from records alone."""
from __future__ import annotations

import lineage
from operators import HUMAN, Operator


def _op(op_id: str, parent: str = HUMAN) -> Operator:
    return Operator(op_id, op_id, "/w", "2026-01-01T00:00:00Z", parent, 0)


def test_a_parent_with_no_record_reads_as_the_person():
    records = [_op("a"), _op("b", "a"), _op("c", "gone"), _op("d", "d")]
    assert lineage.parents(records) == {"a": HUMAN, "b": "a", "c": HUMAN, "d": HUMAN}


def test_children_subtree_and_depth():
    records = [_op("a"), _op("b", "a"), _op("c", "b"), _op("d", "a"), _op("e")]
    assert [op.id for op in lineage.children("a", records)] == ["b", "d"]
    assert [op.id for op in lineage.subtree("a", records)] == ["b", "c", "d"]
    assert lineage.subtree("e", records) == []
    assert [lineage.depth(i, records) for i in "abcde"] == [1, 2, 3, 2, 1]
    assert lineage.depth(HUMAN, records) == 0


def test_tree_puts_each_child_after_its_parent():
    records = [_op("c", "b"), _op("a"), _op("b", "a"), _op("z")]
    assert [(op.id, level) for op, level in lineage.tree(records)] == [
        ("a", 1), ("b", 2), ("c", 3), ("z", 1)]


def test_a_loop_in_hand_edited_records_loses_nobody():
    records = [_op("x", "y"), _op("y", "x"), _op("a")]
    rows = [(op.id, level) for op, level in lineage.tree(records)]
    assert sorted(op_id for op_id, _ in rows) == ["a", "x", "y"]
    assert lineage.depth("x", records) == 2
    assert [op.id for op in lineage.subtree("x", records)] == ["y"]
