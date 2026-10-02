"""The multiplexer names a session with the operator id it was given."""
from __future__ import annotations

import mux


def test_new_session_passes_the_operator_id_through(monkeypatch):
    seen = []
    present = {"now": False}

    def has_session(_session):
        return present["now"]

    def _run(*args):
        seen.append(args)
        present["now"] = True
        return "", "", 0

    client = mux.Mux()
    monkeypatch.setattr(client, "_run", _run)
    monkeypatch.setattr(client, "has_session", has_session)
    client.new_session("op:abcdef01", r"C:\work", ["runner"])
    assert seen[0][2:4] == ("-s", "op:abcdef01")
    assert mux.sanitize_name("op:abcdef01") == "op-abcdef01"
