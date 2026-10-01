"""One spelling of the restart marker, for the two callers that need it.

`supervisor.py` polls it through an `Instance`. `operator handoff` is handed a
seat id on a command line and has no `Instance` to ask, and building one from
an id is wrong because `safe_instance_id` is not idempotent.
"""
from __future__ import annotations

import op


def test_the_function_and_the_property_name_the_same_file():
    """Two spellings of one location is the drift `paths.py` refuses for the
    catalog, and the marker is no different."""
    seat = op.Instance("alpha")
    assert op.restart_marker_for(seat.id) == seat.restart_marker


def test_sanitising_an_id_a_second_time_invents_a_third_name():
    """The hazard `restart_marker_for` exists to let callers avoid.

    Measured, not assumed: this is why `operator handoff` may not build an
    `Instance` from the seat id the preamble advertised to it.
    """
    once = op.safe_instance_id("a.b")
    assert once != "a.b"
    assert op.safe_instance_id(once) != once


def test_the_marker_for_a_sanitised_id_is_the_one_the_supervisor_polls():
    seat = op.Instance("a.b")
    assert op.restart_marker_for(seat.id) == seat.restart_marker
    assert op.restart_marker_for(seat.id).name == seat.id
    assert op.Instance(seat.id).restart_marker != seat.restart_marker


def test_cleanup_removes_only_the_files_a_live_seat_still_owns(tmp_path, monkeypatch):
    """The cut left stop as the only shutdown file. A property added back
    would survive cleanup and keep a dead seat looking owned."""
    monkeypatch.setattr(op, "RESTART_DIR", tmp_path)
    seat = op.Instance("alpha")
    owned = (
        seat.restart_marker, seat.managed_file, seat.spec_file, seat.pid_file,
        seat.exit_file, seat.session_file, seat.loop_pid_file,
        seat.loop_startup_file, seat.stop_marker, seat.loop_args_file,
    )
    for path in owned:
        path.write_text("x", encoding="utf-8")
    seat.cleanup_files()
    assert not any(path.exists() for path in owned)
