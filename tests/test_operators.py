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
    assert operators.find(created.id) == created
    assert operators.find("missing") is None


def test_a_name_collision_is_refused(tmp_path):
    operators.create("Alpha", tmp_path)
    with pytest.raises(operators.BadName, match="already exists"):
        operators.create("alpha", tmp_path / "other")


def test_a_name_equal_to_another_records_id_is_refused(tmp_path):
    first = operators.create("Alpha", tmp_path)
    with pytest.raises(operators.BadName, match="another operator's id"):
        operators.create(first.id, tmp_path)


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


def test_all_operators_skips_a_corrupt_file(tmp_path):
    kept = operators.create("Kept", tmp_path)
    broken = operators.records_dir() / "op-broken1.json"
    broken.write_text("{not json", encoding="utf-8")
    assert operators.all_operators() == [kept]
