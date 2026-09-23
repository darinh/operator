"""Launch argv assembly keeps operator flags in front of `--`."""
from __future__ import annotations

import launch


def test_usage_logging_stays_before_the_terminator(monkeypatch):
    monkeypatch.delenv("COPILOT_OPERATOR_NO_DEBUG_LOG", raising=False)
    out = launch._ensure_usage_logging(["copilot", "--", "some text"])
    assert out == ["copilot", "--log-level", "debug", "--", "some text"]


