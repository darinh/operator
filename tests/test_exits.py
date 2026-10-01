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
    exits.request_restart("alpha")
    assert marker.exists()


# ── what two reviewers found: the seat key, and the guard on it ──


def test_a_seat_name_that_is_not_one_path_component_is_refused(tmp_path,
                                                               monkeypatch):
    """A seat name that is not one path component writes nothing."""
    work = _registered(tmp_path, monkeypatch)
    for bad in ("../escape", ".", "", "a/b", "CON"):
        assert exits.write_handoff(work, bad, "nope") is None, bad
        assert not exits.request_restart(bad), bad


def test_the_restart_marker_is_not_re_sanitised(tmp_path):
    """`safe_instance_id` is not idempotent, so building an `Instance` from an
    id that is already sanitised invents a third name."""
    seat_id = op.safe_instance_id("a.b")
    assert op.safe_instance_id(seat_id) != seat_id, "the hazard is real"
    assert exits.request_restart(seat_id)
    assert (op.RESTART_DIR / seat_id).exists()


