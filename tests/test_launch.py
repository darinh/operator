"""Launch argv assembly keeps operator flags in front of `--`."""
from __future__ import annotations

import launch


def test_usage_logging_stays_before_the_terminator(monkeypatch):
    monkeypatch.delenv("COPILOT_OPERATOR_NO_DEBUG_LOG", raising=False)
    out = launch._ensure_usage_logging(["copilot", "--", "some text"])
    assert out == ["copilot", "--log-level", "debug", "--", "some text"]


def test_usage_logging_still_injects_when_the_tail_names_log_level(monkeypatch):
    monkeypatch.delenv("COPILOT_OPERATOR_NO_DEBUG_LOG", raising=False)
    out = launch._ensure_usage_logging(
        ["copilot", "--", "--log-level=info"])
    assert out == [
        "copilot", "--log-level", "debug", "--", "--log-level=info",
    ]


def test_a_literal_resume_is_not_an_explicit_session():
    assert launch.args_have_explicit_session(
        ["--", "--resume=literal"]) is False
    assert launch.args_have_explicit_session(["--resume=abc"]) is True


def test_the_running_line_says_how_to_attach(monkeypatch, tmp_path):
    import op
    logged = []
    inst = op.Instance("op-abcdef01", "alpha")
    monkeypatch.setattr(launch, "copilot_executable", lambda: "copilot")
    monkeypatch.setattr(launch, "log", logged.append)
    monkeypatch.setattr(launch.time, "sleep", lambda _s: None)
    monkeypatch.setattr(launch.MUX, "has_session", lambda _session: False)
    monkeypatch.setattr(launch.MUX, "new_session", lambda *a, **k: None)
    monkeypatch.setattr(launch.MUX, "set_remain_on_exit", lambda *a, **k: None)
    monkeypatch.setattr(inst, "copilot_pid", lambda: 7)
    monkeypatch.setattr(inst, "claim", lambda _token: None)
    monkeypatch.chdir(tmp_path)
    launch.start_session(inst, ["--agent", "test:agent"], 1, True, "")
    assert logged[-1] == (
        "  Session #1 running (copilot pid=7) — attach with: operator attach alpha")


def test_a_literal_agent_is_not_an_agent_flag():
    assert launch.has_agent_flag(["--", "--agent=evil"]) is False
    assert launch.has_agent_flag(["--agent=x"]) is True


