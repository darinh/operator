"""Shutdown waits for the session to end, not for a metrics capture."""
from __future__ import annotations

import op


def test_metrics_capture_is_not_a_shutdown_wait():
    assert not hasattr(op.session_state, "wait_for_metrics_capture")


def test_wait_for_exit_is_already_done_when_the_pane_is_down(monkeypatch):
    monkeypatch.setattr(op, "is_copilot_running", lambda instance: False)
    assert op.wait_for_exit(object(), timeout=0) is True
