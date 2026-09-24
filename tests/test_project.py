"""`operator project` writes the catalog the existing reader already understands."""
from __future__ import annotations

import paths
from operator_cli import entry as cli
from operator_memory import journal


def _guid(capsys) -> str:
    return capsys.readouterr().out.strip().splitlines()[0]


def test_register_prints_a_guid_the_existing_reader_accepts(tmp_path, monkeypatch,
                                                            capsys):
    cwd = tmp_path / "repo"
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    assert cli.main(["project", "register"]) == 0
    guid = _guid(capsys)
    assert paths.guid_is_usable(guid)
    found = paths.catalog_guid(cwd)
    assert found.guid == guid
    assert not found.undecided


def test_registering_twice_does_not_add_a_second_row(tmp_path, monkeypatch,
                                                     capsys):
    cwd = tmp_path / "repo"
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    assert cli.main(["project", "register"]) == 0
    first = _guid(capsys)
    assert cli.main(["project", "register"]) == 0
    assert _guid(capsys) == first
    catalog = paths.project_catalog_path()
    with open(catalog, "r", encoding="utf-8", errors="replace",
              newline="") as fh:
        rows = [row for row in paths.catalog_rows(fh) if row]
    assert len(rows) == 1
    assert paths.catalog_guid(cwd).guid == first


def test_remember_can_write_after_register(tmp_path, monkeypatch, capsys):
    cwd = tmp_path / "repo"
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    assert cli.main(["project", "register"]) == 0
    capsys.readouterr()
    assert cli.main(["remember", "--instance", "alpha", "--kind", "decision",
                     "chose sqlite"]) == 0
    assert "remembered" in capsys.readouterr().out
    entries = journal.recall(cwd, "alpha")
    assert entries[0]["text"] == "chose sqlite"


def test_list_marks_the_current_directory(tmp_path, monkeypatch, capsys):
    cwd = tmp_path / "here"
    other = tmp_path / "there"
    cwd.mkdir()
    other.mkdir()
    monkeypatch.chdir(cwd)
    assert cli.main(["project", "register"]) == 0
    here = _guid(capsys)
    assert cli.main(["project", "register", str(other)]) == 0
    there = _guid(capsys)
    assert cli.main(["project", "list"]) == 0
    out = capsys.readouterr().out
    assert f"{here}  " in out
    assert "(current)" in out
    here_line = [line for line in out.splitlines() if here in line][0]
    there_line = [line for line in out.splitlines() if there in line][0]
    assert "(current)" in here_line
    assert "(current)" not in there_line


def test_forget_removes_the_row_and_leaves_the_journal(tmp_path, monkeypatch,
                                                       capsys):
    cwd = tmp_path / "repo"
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    assert cli.main(["project", "register"]) == 0
    guid = _guid(capsys)
    assert journal.remember(cwd, "alpha", "gotcha", "rotation is a rename")
    journal_path = paths.project_journal_file(cwd, "alpha")
    assert journal_path is not None and journal_path.exists()
    assert cli.main(["project", "forget", str(cwd)]) == 0
    out = capsys.readouterr().out
    assert guid in out
    assert "journal left on disk" in out
    assert paths.catalog_guid(cwd).guid is None
    assert journal_path.exists()


def test_forget_of_an_unknown_directory_fails(tmp_path, monkeypatch, capsys):
    cwd = tmp_path / "repo"
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    assert cli.main(["project", "forget", str(cwd)]) == 1
    err = capsys.readouterr().err
    assert "not a registered project" in err
    assert "operator project register" in err


def test_register_of_a_missing_path_fails(tmp_path, capsys):
    missing = tmp_path / "nope"
    assert cli.main(["project", "register", str(missing)]) == 2
    assert "not a directory" in capsys.readouterr().err


def test_an_empty_catalog_lists_nothing(capsys):
    assert cli.main(["project", "list"]) == 0
    assert "No registered projects." in capsys.readouterr().out


def test_forget_removes_a_row_whose_directory_is_gone(tmp_path, monkeypatch,
                                                      capsys):
    cwd = tmp_path / "repo"
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    assert cli.main(["project", "register"]) == 0
    guid = _guid(capsys)
    monkeypatch.chdir(tmp_path)
    cwd.rmdir()
    assert not cwd.exists()
    assert cli.main(["project", "forget", str(cwd)]) == 0
    out = capsys.readouterr().out
    assert guid in out
    catalog = paths.project_catalog_path()
    with open(catalog, "r", encoding="utf-8", errors="replace",
              newline="") as fh:
        rows = [row for row in paths.catalog_rows(fh) if row]
    assert rows == []


def test_project_without_a_subcommand_is_usage(capsys):
    assert cli.main(["project"]) == 2
    err = capsys.readouterr().err.lower()
    assert "usage:" in err
    assert "register" in err
