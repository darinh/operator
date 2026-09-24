"""Tombstones must still write when the journal is at its size limit."""
from __future__ import annotations

import pytest

import paths
from operator_memory import journal


@pytest.fixture
def project(tmp_path, monkeypatch):
    home = tmp_path / "operator-home"
    (home / "projects").mkdir(parents=True)
    monkeypatch.setenv("COPILOT_OPERATOR_HOME", str(home))
    monkeypatch.setattr(paths, "OPERATOR_HOME", home, raising=False)
    cwd = tmp_path / "project"
    cwd.mkdir()
    guid = "11111111-2222-3333-4444-555555555555"
    (home / "projects" / guid).mkdir(parents=True)
    (home / "projects" / "catalog.csv").write_text(
        f'"{cwd}",{guid}\n', encoding="utf-8")
    return cwd


def test_forget_writes_through_the_size_limit(project, monkeypatch):
    monkeypatch.setattr(journal, "MAX_JOURNAL_BYTES", 200)
    entry_id = journal.remember(project, "prism", "gotcha", "keep me")
    assert entry_id
    while journal.remember(project, "prism", "gotcha", "y" * 40):
        pass
    assert journal.remember(project, "prism", "gotcha", "nope") is None
    assert journal.forget(project, "prism", entry_id)
    assert journal.remember(project, "prism", "gotcha", "nope") is None
    texts = [e["text"] for e in journal.recall(project, "prism")]
    assert "keep me" not in texts
