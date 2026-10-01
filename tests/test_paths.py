"""catalog_guid identity: resolved strings, then samefile."""
from __future__ import annotations

import os
from pathlib import Path

import paths
from operator_cli.project import ensure_registered


def _alias_catalog(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / "projects").mkdir(parents=True)
    monkeypatch.setenv("COPILOT_OPERATOR_HOME", str(home))
    stored = tmp_path / "stored"
    alias = tmp_path / "alias"
    stored.mkdir()
    alias.mkdir()
    guid = "11111111-2222-3333-4444-555555555555"
    catalog = home / "projects" / "catalog.csv"
    catalog.write_text(f'"{stored}",{guid}\n', encoding="utf-8")
    return home, stored, alias, guid, catalog


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


def _assert_probe_failure_is_undecided(alias, guid, catalog):
    found = paths.catalog_guid(alias)
    assert found.guid is None
    assert found.undecided is True
    before = catalog.read_text(encoding="utf-8")
    rc, new_guid, created = ensure_registered(str(alias))
    assert rc == 1
    assert created is False
    assert new_guid == ""
    assert catalog.read_text(encoding="utf-8") == before
    assert guid in before
    assert before.count("\n") == 1


def test_samefile_permission_error_is_undecided(tmp_path, monkeypatch):
    _home, _stored, alias, guid, catalog = _alias_catalog(tmp_path, monkeypatch)

    def boom(*_a, **_k):
        raise PermissionError("denied")

    monkeypatch.setattr(os.path, "samefile", boom)
    _assert_probe_failure_is_undecided(alias, guid, catalog)


def test_samefile_timeout_error_is_undecided(tmp_path, monkeypatch):
    _home, _stored, alias, guid, catalog = _alias_catalog(tmp_path, monkeypatch)

    def boom(*_a, **_k):
        raise TimeoutError("slow")

    monkeypatch.setattr(os.path, "samefile", boom)
    _assert_probe_failure_is_undecided(alias, guid, catalog)


def test_directory_gone_between_exists_and_samefile_is_undecided(
        tmp_path, monkeypatch):
    _home, _stored, alias, guid, catalog = _alias_catalog(tmp_path, monkeypatch)

    def boom(*_a, **_k):
        raise FileNotFoundError("gone")

    monkeypatch.setattr(os.path, "samefile", boom)
    _assert_probe_failure_is_undecided(alias, guid, catalog)


def test_a_seat_name_that_is_not_one_path_component_addresses_nothing(
        tmp_path, monkeypatch):
    """The gate `project_journal_file` has always had, arriving here late.

    Its only caller was the supervisor, which passes `instance.id` and cannot
    produce a bad one. `operator handoff` takes the name from a command line,
    where `--instance ../elsewhere` resolved to a file outside `handoff/`.
    """
    _home, stored, _alias, _guid, _catalog = _alias_catalog(tmp_path, monkeypatch)
    for bad in ("../escape", "a/b", "a\\b", "x.", "CON"):
        assert paths.project_handoff_file(stored, bad) is None, bad


def test_an_empty_seat_still_answers_with_the_unmigrated_handoff(
        tmp_path, monkeypatch):
    """An empty id is not a bad seat name, it is the pre-migration question,
    and a project that never migrated still has `next-session.md`."""
    _home, stored, _alias, _guid, _catalog = _alias_catalog(tmp_path, monkeypatch)
    assert paths.project_handoff_file(stored, "").name == "next-session.md"


def test_a_usable_seat_name_still_resolves_under_handoff(tmp_path, monkeypatch):
    """Positive control: the gate must not refuse ordinary seats."""
    _home, stored, _alias, _guid, _catalog = _alias_catalog(tmp_path, monkeypatch)
    found = paths.project_handoff_file(stored, "a-b-69f664")
    assert found.name == "a-b-69f664.md"
    assert found.parent.name == "handoff"
