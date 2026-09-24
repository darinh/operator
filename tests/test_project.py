"""`operator project` writes the catalog the existing reader already understands."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

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


def test_forget_rejects_an_empty_path(tmp_path, monkeypatch, capsys):
    cwd = tmp_path / "repo"
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    assert cli.main(["project", "register"]) == 0
    capsys.readouterr()
    assert cli.main(["project", "forget", ""]) == 2
    err = capsys.readouterr().err
    assert "directory path is needed" in err
    assert paths.catalog_guid(cwd).guid


def test_register_reuses_guid_when_samefile_says_so(tmp_path, monkeypatch,
                                                    capsys):
    first = tmp_path / "one"
    alias = tmp_path / "two"
    first.mkdir()
    alias.mkdir()
    monkeypatch.chdir(first)
    assert cli.main(["project", "register"]) == 0
    guid = _guid(capsys)

    def fake_samefile(left, right):
        try:
            pair = {str(Path(left).resolve()), str(Path(right).resolve())}
        except (OSError, ValueError):
            return False
        return {str(first.resolve()), str(alias.resolve())} <= pair or (
            Path(left).resolve() == Path(right).resolve())

    monkeypatch.setattr(os.path, "samefile", fake_samefile)
    assert cli.main(["project", "register", str(alias)]) == 0
    assert _guid(capsys) == guid
    catalog = paths.project_catalog_path()
    with open(catalog, "r", encoding="utf-8", errors="replace",
              newline="") as fh:
        rows = [row for row in paths.catalog_rows(fh) if row]
    assert len(rows) == 1


def test_two_processes_registering_keep_both_rows(tmp_path, monkeypatch):
    import time
    home = tmp_path / "home"
    (home / "projects").mkdir(parents=True)
    monkeypatch.setenv("COPILOT_OPERATOR_HOME", str(home))
    a = tmp_path / "a"
    b = tmp_path / "b"
    a.mkdir()
    b.mkdir()
    gate = tmp_path / "go"
    root = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    env["COPILOT_OPERATOR_HOME"] = str(home)
    env["OPERATOR_CATALOG_PAUSE"] = "0.4"
    env["PYTHONPATH"] = os.pathsep.join([
        str(root), str(root / "operator_kernel"), str(root / "operator_fleet"),
        env.get("PYTHONPATH", ""),
    ])
    child = (
        "import sys, time\n"
        "from pathlib import Path\n"
        "gate = Path(sys.argv[2])\n"
        "while not gate.exists():\n"
        "    time.sleep(0.01)\n"
        "from operator_cli.project import ensure_registered\n"
        "rc, guid, created = ensure_registered(sys.argv[1])\n"
        "print(guid)\n"
        "raise SystemExit(rc)\n"
    )
    procs = [
        subprocess.Popen(
            [sys.executable, "-c", child, str(path), str(gate)],
            env=env, cwd=str(tmp_path),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        for path in (a, b)
    ]
    time.sleep(0.3)
    gate.write_text("go", encoding="utf-8")
    results = [p.communicate(timeout=30) for p in procs]
    codes = [p.returncode for p in procs]
    assert codes == [0, 0], results
    guids = [out.strip() for out, _err in results]
    assert len(set(guids)) == 2
    catalog = home / "projects" / "catalog.csv"
    text = catalog.read_text(encoding="utf-8")
    assert guids[0] in text and guids[1] in text


def test_project_without_a_subcommand_is_usage(capsys):
    assert cli.main(["project"]) == 2
    err = capsys.readouterr().err.lower()
    assert "usage:" in err
    assert "register" in err
