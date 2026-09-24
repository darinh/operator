"""catalog_guid identity: resolved strings, then samefile."""
from __future__ import annotations

import os
from pathlib import Path

import paths


def test_catalog_guid_follows_samefile(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / "projects").mkdir(parents=True)
    monkeypatch.setenv("COPILOT_OPERATOR_HOME", str(home))
    stored = tmp_path / "stored"
    alias = tmp_path / "alias"
    stored.mkdir()
    alias.mkdir()
    guid = "11111111-2222-3333-4444-555555555555"
    (home / "projects" / "catalog.csv").write_text(
        f'"{stored}",{guid}\n', encoding="utf-8")

    def fake_samefile(left, right):
        try:
            pair = {Path(left).resolve(), Path(right).resolve()}
        except (OSError, ValueError):
            return False
        return pair <= {stored.resolve(), alias.resolve()} and len(pair) <= 2

    monkeypatch.setattr(os.path, "samefile", fake_samefile)
    assert paths.catalog_guid(stored).guid == guid
    assert paths.catalog_guid(alias).guid == guid
