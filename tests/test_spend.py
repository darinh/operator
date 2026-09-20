"""The kernel reads a spend figure. It never computes one."""
from __future__ import annotations

import json
from pathlib import Path


def test_seat_spend_is_none_when_the_file_is_absent(tmp_path):
    import spend
    assert spend.seat_spend(tmp_path, "seat") is None


def test_seat_spend_is_none_when_the_file_is_unreadable(tmp_path):
    import spend
    path = spend.spend_path(tmp_path, "seat")
    path.parent.mkdir(parents=True)
    path.write_text("{not json", encoding="utf-8")
    assert spend.seat_spend(tmp_path, "seat") is None


def test_seat_spend_is_none_when_amount_is_a_bool(tmp_path):
    import spend
    path = spend.spend_path(tmp_path, "seat")
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"amount": True, "unit": "usd", "source": "x"}),
                    encoding="utf-8")
    assert spend.seat_spend(tmp_path, "seat") is None


def test_seat_spend_returns_the_amount(tmp_path):
    import spend
    path = spend.spend_path(tmp_path, "seat")
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(
        {"amount": 4.5, "unit": "usd", "source": "ingest"}), encoding="utf-8")
    assert spend.seat_spend(tmp_path, "seat") == 4.5


def test_unknown_spend_is_not_zero(tmp_path):
    import spend
    assert spend.seat_spend(tmp_path, "missing") is None
    assert spend.seat_spend(tmp_path, "missing") != 0


def test_a_path_shaped_seat_id_does_not_escape_the_spend_dir(tmp_path):
    import spend
    outside = tmp_path / "secret.json"
    outside.write_text(json.dumps({"amount": 9}), encoding="utf-8")
    assert spend.seat_spend(tmp_path, "../secret") is None
    assert spend.seat_spend(tmp_path, str(Path("..") / "secret")) is None


def test_spend_blocks_only_when_ceiling_and_amount_are_both_known(tmp_path):
    import spend
    path = spend.spend_path(tmp_path, "seat")
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(
        {"amount": 10, "unit": "usd", "source": "t"}), encoding="utf-8")
    assert spend.spend_blocks(tmp_path, "seat", None) is None
    assert spend.spend_blocks(tmp_path, "other", 5) is None
    blocked = spend.spend_blocks(tmp_path, "seat", 10)
    assert blocked is not None
    assert blocked[0] == "spend-ceiling"
    assert spend.spend_blocks(tmp_path, "seat", 10.1) is None
