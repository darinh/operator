"""Operator records outlive a clean stop. The id does not move. The name does."""
from __future__ import annotations

import json

import pytest

import operators


def test_create_then_find_by_name_and_by_id(tmp_path):
    created = operators.create("Alpha", tmp_path)
    assert created.id.startswith("op-")
    assert len(created.id) == len("op-") + 8
    assert created.name == "Alpha"
    assert created.cwd == str(tmp_path.resolve())
    assert operators.find("alpha") == created
    assert operators.find(" alpha ") == created
    assert operators.find(created.id) == created
    assert operators.find(f" {created.id} ") == created
    assert operators.find("missing") is None


def test_a_name_collision_is_refused(tmp_path):
    operators.create("Alpha", tmp_path)
    with pytest.raises(operators.BadName, match="already exists"):
        operators.create("alpha", tmp_path / "other")


def test_a_name_equal_to_another_records_id_is_refused(tmp_path):
    first = operators.create("Alpha", tmp_path)
    with pytest.raises(operators.BadName, match="another operator's id"):
        operators.create(first.id, tmp_path)


def test_lineage_is_stored_and_survives_a_rename(tmp_path):
    parent = operators.create("Alpha", tmp_path)
    child = operators.create("Bravo", tmp_path, parent=parent.id, started_by_pid=4242)
    assert operators.find("bravo") == child
    assert (child.parent, child.started_by_pid) == (parent.id, 4242)
    renamed = operators.rename(child, "Charlie")
    assert (renamed.parent, renamed.started_by_pid) == (parent.id, 4242)
    assert operators.find("charlie") == renamed


def test_a_record_from_before_lineage_reads_as_started_by_a_person(tmp_path):
    directory = operators.records_dir()
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "op-0ld00001.json").write_text(json.dumps(
        {"id": "op-0ld00001", "name": "old", "cwd": str(tmp_path),
         "created": "2026-01-01T00:00:00Z"}), encoding="utf-8")
    found = operators.find("old")
    assert (found.parent, found.started_by_pid) == (operators.HUMAN, 0)
    assert operators.unreadable() == []


def test_human_is_not_a_name_an_operator_can_take(tmp_path):
    for name in ("human", "Human", " HUMAN "):
        assert "reserved" in operators.name_problem(name)
    first = operators.create("Alpha", tmp_path)
    with pytest.raises(operators.BadName, match="reserved"):
        operators.rename(first, "human")


def test_rename_keeps_the_id_and_the_files(tmp_path):
    from config import RESTART_DIR
    created = operators.create("Alpha", tmp_path)
    state = RESTART_DIR / f"{created.id}.state"
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text("SESSION_NUM=1\n", encoding="utf-8")
    record_path = operators.records_dir() / f"{created.id}.json"
    renamed = operators.rename(created, "Bravo")
    assert renamed.id == created.id
    assert renamed.name == "Bravo"
    assert renamed.cwd == created.cwd
    assert record_path.is_file()
    assert json.loads(record_path.read_text(encoding="utf-8"))["name"] == "Bravo"
    assert state.read_text(encoding="utf-8") == "SESSION_NUM=1\n"
    assert operators.find("bravo").id == created.id
    assert operators.find("alpha") is None


def test_remove_deletes_only_the_record(tmp_path):
    from config import RESTART_DIR
    created = operators.create("Alpha", tmp_path)
    state = RESTART_DIR / f"{created.id}.state"
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text("kept\n", encoding="utf-8")
    operators.remove(created)
    assert operators.find("Alpha") is None
    assert not (operators.records_dir() / f"{created.id}.json").exists()
    assert state.read_text(encoding="utf-8") == "kept\n"


def test_all_operators_skips_a_corrupt_file_and_names_it(tmp_path):
    kept = operators.create("Kept", tmp_path)
    broken = operators.records_dir() / "op-broken1.json"
    broken.write_text("{not json", encoding="utf-8")
    assert operators.all_operators() == [kept]
    assert operators.unreadable() == [broken]


def test_a_record_whose_name_has_spaces_around_it_is_unreadable(tmp_path):
    """create and rename never store one, and find strips what it is given, so
    no verb could reach it by the name the list would show."""
    kept = operators.create("alpha", tmp_path)
    padded = operators.records_dir() / "op-padded1.json"
    padded.write_text(json.dumps({"id": "op-padded1", "name": " alpha ",
                                  "cwd": str(tmp_path), "created": "2026-10-07"}),
                      encoding="utf-8")
    assert operators.all_operators() == [kept]
    assert operators.unreadable() == [padded]
    assert operators.find(" alpha ") == kept
