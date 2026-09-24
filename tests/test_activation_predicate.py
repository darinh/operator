"""The enabled predicate must stay the same in every reader of extensions.json.

Fleet cannot import operator_extensions, so the check is written three times.
This table is the contract they have to keep.
"""
from __future__ import annotations

import json

import pytest

import fleet_host
from operator_cli import ext
from operator_extensions import activation


CASES = [
    ({"enabled": True}, True),
    ({"enabled": False}, False),
    ({"enabled": "true"}, False),
    ({"enabled": 1}, False),
    ({}, False),
    ("yes", False),
    ({"enabled": True, "extra": 9}, True),
]


class _Ext:
    def __init__(self, name):
        self.name = name


@pytest.mark.parametrize("entry, expect", CASES)
def test_activation_readers_agree(entry, expect, tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("COPILOT_OPERATOR_HOME", str(home))
    (home / "extensions.json").write_text(
        json.dumps({"probe": entry}), encoding="utf-8")
    act = activation.settings("probe") is not None
    watching, inert = fleet_host._watching_and_inert(home, [_Ext("probe")])
    fleet = "probe" in watching
    ext._bootstrap()
    loaded = ext._load()
    cli = (isinstance(loaded.get("probe"), dict)
           and loaded.get("probe", {}).get("enabled") is True)
    assert act is expect
    assert fleet is expect
    assert cli is expect
