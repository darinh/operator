"""One spelling of the restart marker, for the two callers that need it.

`supervisor.py` polls it through an `Instance`. `operator handoff` is handed an
operator id and does not hold an `Instance`.
"""
from __future__ import annotations

import op


def test_the_function_and_the_property_name_the_same_file():
    """Two spellings of one location is the drift `paths.py` refuses for the
    catalog, and the marker is no different."""
    operator = op.Instance("alpha")
    assert op.restart_marker_for(operator.id) == operator.restart_marker


def test_building_an_instance_from_an_id_addresses_the_same_marker():
    operator = op.Instance("op-abcdef01", "a.b")
    assert operator.id == "op-abcdef01"
    assert operator.display_name == "a.b"
    assert op.restart_marker_for(operator.id) == operator.restart_marker
    assert op.Instance(operator.id).restart_marker == operator.restart_marker


def test_cleanup_removes_only_the_files_a_live_operator_still_owns(tmp_path, monkeypatch):
    """The cut left stop as the only shutdown file. A property added back
    would survive cleanup and keep a dead operator looking owned."""
    monkeypatch.setattr(op, "RESTART_DIR", tmp_path)
    operator = op.Instance("alpha")
    owned = (
        operator.restart_marker, operator.managed_file, operator.spec_file, operator.pid_file,
        operator.custody_file, operator.exit_file, operator.session_file, operator.loop_pid_file,
        operator.loop_startup_file, operator.stop_marker, operator.loop_args_file,
    )
    for path in owned:
        path.write_text("x", encoding="utf-8")
    operator.cleanup_files()
    assert not any(path.exists() for path in owned)
