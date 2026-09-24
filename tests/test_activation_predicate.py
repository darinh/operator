"""Drive the real CLI list against the shared enabled table.

Fleet cannot import operator_extensions, so the check is written three times.
This table is the contract. The CLI half is ext.main(["list"]), not a copy
of the predicate.
"""
from __future__ import annotations

import json

import pytest

import extensions
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
def test_ext_list_matches_the_activation_table(entry, expect, tmp_path,
                                               monkeypatch, capsys):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("COPILOT_OPERATOR_HOME", str(home))
    (home / "extensions.json").write_text(
        json.dumps({"probe": entry}), encoding="utf-8")
    monkeypatch.setattr(extensions, "discover",
                        lambda *a, **k: ([_Ext("probe")], []))
    act = activation.settings("probe") is not None
    watching, _inert = fleet_host._watching_and_inert(home, [_Ext("probe")])
    assert ext.main(["list"]) == 0
    out = capsys.readouterr().out
    line = [row for row in out.splitlines() if row.startswith("probe  ")][0]
    tokens = line.split()
    listed = "enabled" in tokens
    assert act is expect
    assert ("probe" in watching) is expect
    assert listed is expect
    if expect:
        assert "disabled" not in tokens
    else:
        assert "enabled" not in tokens
