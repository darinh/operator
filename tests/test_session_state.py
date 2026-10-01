"""Shutdown waits for the session to end."""
from __future__ import annotations

import op


def test_wait_for_exit_is_already_done_when_the_pane_is_down(monkeypatch):
    monkeypatch.setattr(op, "is_copilot_running", lambda instance: False)
    assert op.wait_for_exit(object(), timeout=0) is True
