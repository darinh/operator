"""`operator ext` writes the activation file a human used to have to hand-edit."""
from __future__ import annotations

import json

import extensions
from operator_cli import entry as cli
from operator_extensions import activation


class _Ext:
    def __init__(self, name: str):
        self.name = name
        self.target = "pkg." + name


def _three(_entry_points=None):
    return ([_Ext("worktree-guard"), _Ext("worktree-janitor"),
             _Ext("seat-watch")], [])


def test_list_shows_registered_extensions_as_disabled(monkeypatch, capsys):
    monkeypatch.setattr(extensions, "discover", _three)
    assert cli.main(["ext", "list"]) == 0
    out = capsys.readouterr().out
    for name in ("worktree-guard", "worktree-janitor", "seat-watch"):
        assert name in out
        line = [row for row in out.splitlines() if row.startswith(name + "  ")][0]
        assert "disabled" in line
        assert "enabled" not in line.split()


def test_enable_then_list_shows_enabled(monkeypatch, capsys):
    monkeypatch.setattr(extensions, "discover", _three)
    assert cli.main(["ext", "enable", "seat-watch"]) == 0
    assert "enabled seat-watch" in capsys.readouterr().out
    assert cli.main(["ext", "list"]) == 0
    lines = capsys.readouterr().out.splitlines()
    watch = [row for row in lines if row.startswith("seat-watch  ")][0]
    assert "enabled" in watch.split()
    guard = [row for row in lines if row.startswith("worktree-guard  ")][0]
    assert "disabled" in guard


def test_enable_keeps_unknown_settings(monkeypatch, capsys):
    monkeypatch.setattr(extensions, "discover", _three)
    path = activation.config_path()
    path.write_text(json.dumps({
        "seat-watch": {"enabled": False, "mystery": "keep-me", "failures": 9},
    }), encoding="utf-8")
    assert cli.main(["ext", "enable", "seat-watch"]) == 0
    capsys.readouterr()
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["seat-watch"]["enabled"] is True
    assert data["seat-watch"]["mystery"] == "keep-me"
    assert data["seat-watch"]["failures"] == 9
    assert cli.main(["ext", "list"]) == 0
    out = capsys.readouterr().out
    assert "mystery" in out
    assert "keep-me" in out


def test_enable_set_parses_json_values(monkeypatch):
    monkeypatch.setattr(extensions, "discover", _three)
    assert cli.main(["ext", "enable", "seat-watch", "--set", "failures=3",
                     "--set", "roots=[\"~/repos\"]"]) == 0
    data = json.loads(activation.config_path().read_text(encoding="utf-8"))
    assert data["seat-watch"]["enabled"] is True
    assert data["seat-watch"]["failures"] == 3
    assert data["seat-watch"]["roots"] == ["~/repos"]


def test_disable_preserves_settings(monkeypatch, capsys):
    monkeypatch.setattr(extensions, "discover", _three)
    assert cli.main(["ext", "enable", "seat-watch", "--set", "failures=3"]) == 0
    capsys.readouterr()
    assert cli.main(["ext", "disable", "seat-watch"]) == 0
    assert "disabled seat-watch" in capsys.readouterr().out
    data = json.loads(activation.config_path().read_text(encoding="utf-8"))
    assert data["seat-watch"]["enabled"] is False
    assert data["seat-watch"]["failures"] == 3


def test_disable_an_uninstalled_name_still_in_config(monkeypatch, capsys):
    monkeypatch.setattr(extensions, "discover", _three)
    path = activation.config_path()
    path.write_text(json.dumps({
        "gone-ext": {"enabled": True, "mystery": "keep-me"},
    }), encoding="utf-8")
    assert cli.main(["ext", "disable", "gone-ext"]) == 0
    capsys.readouterr()
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["gone-ext"]["enabled"] is False
    assert data["gone-ext"]["mystery"] == "keep-me"


def test_enable_of_an_unknown_name_fails(monkeypatch, capsys):
    monkeypatch.setattr(extensions, "discover", _three)
    assert cli.main(["ext", "enable", "no-such"]) == 1
    err = capsys.readouterr().err
    assert "not a registered extension" in err
    assert "operator ext list" in err
    assert not activation.config_path().exists()
