"""Handoff writes and the restart marker the supervisor polls."""
from __future__ import annotations

import exits
import op
import paths
from operator_cli.project import ensure_registered


def _registered(tmp_path, monkeypatch):
    """A project the catalog knows about, which is what a handoff needs."""
    monkeypatch.chdir(tmp_path)
    assert ensure_registered(tmp_path)[0] == 0
    assert paths.catalog_guid(tmp_path).guid
    return tmp_path


def test_an_unregistered_project_is_refused_rather_than_guessed(tmp_path,
                                                                monkeypatch):
    """`None` is `project_handoff_file`'s answer for "not registered", and it
    must not become a path under the projects root itself."""
    monkeypatch.chdir(tmp_path)
    assert paths.catalog_guid(tmp_path).guid is None
    assert exits.write_handoff(tmp_path, "alpha", "s") is None


def test_the_restart_marker_is_the_one_the_supervisor_polls(tmp_path):
    """`supervisor.py` watches `instance.restart_marker` and nothing else."""
    marker = op.Instance("alpha").restart_marker
    assert not marker.exists()
    assert exits.request_restart("alpha", 3)
    assert marker.read_text(encoding="utf-8") == '{"id": "alpha", "session": 3}'


# ── what two reviewers found: the operator key, and the guard on it ──


def test_a_operator_name_that_is_not_one_path_component_is_refused(tmp_path,
                                                               monkeypatch):
    """An operator name that is not one path component writes nothing."""
    work = _registered(tmp_path, monkeypatch)
    for bad in ("../escape", ".", "", "a/b", "CON"):
        assert exits.write_handoff(work, bad, "nope") is None, bad
        assert not exits.request_restart(bad, 1), bad


def test_the_restart_marker_is_addressed_by_the_operator_id(tmp_path):
    op_id = "op-abcdef01"
    assert exits.request_restart(op_id, 1)
    assert (op.RESTART_DIR / op_id).exists()
    assert op.Instance(op_id).restart_marker == op.RESTART_DIR / op_id


