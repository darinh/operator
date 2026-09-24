"""Unregistered vs empty is not the same fact, and the exit code has to say so."""
from __future__ import annotations

import paths
from operator_cli import seat as cli


def test_recall_on_an_unregistered_directory_exits_nonzero(tmp_path, monkeypatch,
                                                           capsys):
    monkeypatch.chdir(tmp_path)
    assert cli.main(["--instance", "alpha", "recall"]) == 1
    captured = capsys.readouterr()
    assert "not a registered project" in captured.err
    assert "operator project register" in captured.err
    assert "nothing recorded" not in captured.out


def test_forget_on_an_unregistered_directory_names_the_fix(tmp_path, monkeypatch,
                                                           capsys):
    monkeypatch.chdir(tmp_path)
    assert cli.main(["--instance", "alpha", "forget", "deadbeef"]) == 1
    err = capsys.readouterr().err
    assert "not a registered project" in err
    assert "operator project register" in err
    assert "nothing written" not in err


def test_remember_on_an_unregistered_directory_names_the_fix(tmp_path,
                                                             monkeypatch,
                                                             capsys):
    monkeypatch.chdir(tmp_path)
    assert cli.main(["--instance", "alpha", "remember", "--kind", "decision",
                     "chose sqlite"]) == 1
    err = capsys.readouterr().err
    assert "not a registered project" in err
    assert "operator project register" in err
    assert "size limit" not in err
    assert "usable" not in err


def test_recall_on_an_empty_registered_journal_still_succeeds(tmp_path,
                                                              monkeypatch,
                                                              capsys):
    home = tmp_path / "home"
    (home / "projects").mkdir(parents=True)
    monkeypatch.setenv("COPILOT_OPERATOR_HOME", str(home))
    cwd = tmp_path / "repo"
    cwd.mkdir()
    guid = "11111111-2222-3333-4444-555555555555"
    (home / "projects" / guid).mkdir()
    (home / "projects" / "catalog.csv").write_text(
        f'"{cwd}",{guid}\n', encoding="utf-8")
    monkeypatch.chdir(cwd)
    assert paths.catalog_guid(cwd).guid == guid
    assert cli.main(["--instance", "alpha", "recall"]) == 0
    assert "nothing recorded" in capsys.readouterr().out


def test_remember_names_an_unusable_seat(tmp_path, monkeypatch, capsys):
    cwd = tmp_path / "repo"
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    from operator_cli import entry
    assert entry.main(["project", "register"]) == 0
    capsys.readouterr()
    assert cli.main(["--instance", "D:other", "remember", "--kind", "gotcha",
                     "x"]) == 1
    err = capsys.readouterr().err
    assert "seat name is not usable" in err
    assert "registered project" not in err
