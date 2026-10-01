"""`operator list` names each running operator and its supervisor pid."""
from __future__ import annotations

from operator_cli import listing


class _Inst:
    def __init__(self, name):
        self.display_name = name
        self.id = name


def test_list_names_each_running_operator_and_its_pid(monkeypatch, capsys):
    import supervisor_control
    monkeypatch.setattr(supervisor_control, "active_instances",
                        lambda: [_Inst("alpha"), _Inst("bravo")])
    monkeypatch.setattr(listing, "_running_loop_pid",
                        lambda inst: 11 if inst.display_name == "alpha" else None)
    assert listing.list_instances() == 0
    out = capsys.readouterr().out
    assert "alpha  pid 11" in out
    assert "bravo" in out
    assert "pid" not in out.split("bravo", 1)[1]


def test_list_says_so_when_nothing_is_running(monkeypatch, capsys):
    import supervisor_control
    monkeypatch.setattr(supervisor_control, "active_instances", lambda: [])
    assert listing.list_instances() == 0
    assert capsys.readouterr().out.strip() == "No running seats."
