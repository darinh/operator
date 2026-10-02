"""Start, rename, and delete through the verb module, not only the front door."""
from __future__ import annotations

import operators
from operator_cli import lifecycle


def test_rename_prints_the_old_and_new_names(tmp_path, capsys):
    record = operators.create("alpha", tmp_path)
    assert lifecycle.rename(["alpha", "bravo"]) == 0
    assert operators.find("bravo").id == record.id
    assert capsys.readouterr().out.strip() == "renamed alpha to bravo"


def test_delete_refuses_a_running_operator(tmp_path, monkeypatch, capsys):
    import supervisor_control
    record = operators.create("alpha", tmp_path)
    monkeypatch.setattr(supervisor_control, "active_instances",
                        lambda: [record.instance()])
    assert lifecycle.delete(["alpha", "--yes"]) == 1
    assert capsys.readouterr().err.strip() == "stop it first: operator stop alpha"
    assert operators.find("alpha") is not None
