"""ancestry names the processes above a pid, or says the table could not be read."""
from __future__ import annotations

import process_tree


def test_a_readable_table_is_nearest_parent_first(monkeypatch):
    table = {
        10: (5, r"C:\bin\copilot.exe"),
        5: (1, "python.exe"),
        1: (0, "init"),
    }
    monkeypatch.setattr(process_tree, "IS_WINDOWS", True)
    monkeypatch.setattr(process_tree, "_win_process_table", lambda: table)
    monkeypatch.setattr(process_tree, "_win_image_path",
                        lambda pid: f"C:\\bin\\{pid}.exe")

    chain = process_tree.ancestry(10)

    assert [row["pid"] for row in chain] == [10, 5, 1]
    assert chain[0]["name"] == "copilot.exe"
    assert chain[0]["path"] == r"C:\bin\10.exe"
    assert chain[1]["name"] == "python.exe"


def test_an_unreadable_table_is_none_not_an_empty_list(monkeypatch):
    monkeypatch.setattr(process_tree, "IS_WINDOWS", True)
    monkeypatch.setattr(process_tree, "_win_process_table", lambda: None)

    assert process_tree.ancestry(10) is None


def test_a_cycle_stops_instead_of_spinning(monkeypatch):
    table = {2: (3, "a.exe"), 3: (2, "b.exe")}
    monkeypatch.setattr(process_tree, "IS_WINDOWS", False)
    monkeypatch.setattr(process_tree, "_procfs_available", lambda: False)
    monkeypatch.setattr(process_tree, "_ps_process_table", lambda: table)

    chain = process_tree.ancestry(2)

    assert [row["pid"] for row in chain] == [2, 3]


def test_the_walk_stops_at_the_limit(monkeypatch):
    table = {4: (3, "d"), 3: (2, "c"), 2: (1, "b"), 1: (0, "a")}
    monkeypatch.setattr(process_tree, "IS_WINDOWS", False)
    monkeypatch.setattr(process_tree, "_procfs_available", lambda: False)
    monkeypatch.setattr(process_tree, "_ps_process_table", lambda: table)

    chain = process_tree.ancestry(4, limit=2)

    assert [row["pid"] for row in chain] == [4, 3]


def test_a_posix_read_failure_is_none_not_a_prefix(monkeypatch):
    def unreadable(pid):
        raise process_tree._TreeUnreadable("permission")

    monkeypatch.setattr(process_tree, "IS_WINDOWS", False)
    monkeypatch.setattr(process_tree, "_procfs_available", lambda: True)
    monkeypatch.setattr(process_tree, "_posix_parent", unreadable)

    assert process_tree.ancestry(40) is None


def test_this_process_has_a_chain_or_an_unreadable_table():
    chain = process_tree.ancestry()
    if chain is None:
        return
    assert chain
    assert all(isinstance(row["pid"], int) and row["pid"] > 0 for row in chain)
    assert all("name" in row and "path" in row for row in chain)
