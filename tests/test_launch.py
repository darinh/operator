"""Launch argv assembly keeps operator flags before `--` and makes the tail the task."""
from __future__ import annotations

import json

import pytest

import launch


@pytest.fixture
def session(monkeypatch, tmp_path):
    import op
    inst = op.Instance("op-abcdef01", "alpha")
    monkeypatch.setattr(launch, "copilot_executable", lambda: "copilot")
    monkeypatch.setattr(launch.time, "sleep", lambda _s: None)
    monkeypatch.setattr(launch.MUX, "has_session", lambda _session: False)
    monkeypatch.setattr(launch.MUX, "new_session", lambda *a, **k: None)
    monkeypatch.setattr(launch.MUX, "set_remain_on_exit", lambda *a, **k: None)
    monkeypatch.setattr(inst, "copilot_pid", lambda: 7)
    monkeypatch.setattr(inst, "claim", lambda _token: None)
    monkeypatch.chdir(tmp_path)
    return inst


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


def test_the_running_line_says_how_to_attach(monkeypatch, session):
    logged = []
    monkeypatch.setattr(launch, "log", logged.append)
    launch.start_session(session, ["--agent", "test:agent"], 1, True, "")
    assert logged[-1] == (
        "  Session #1 running (copilot pid=7) — attach with: operator attach alpha")


def test_the_literal_tail_reaches_copilot_as_the_task(monkeypatch, session):
    monkeypatch.delenv("COPILOT_OPERATOR_NO_DEBUG_LOG", raising=False)
    launch.start_session(session, ["--model", "m", "--", "--fresh", "fix", "it"],
                         1, True, "Preamble.")
    spec = json.loads(session.spec_file.read_text(encoding="utf-8"))
    assert spec["argv"] == ["copilot", "--model", "m",
                            "-i", "Preamble. Task: --fresh fix it",
                            "--log-level", "debug"]


@pytest.mark.parametrize("blank", ["", " "])
def test_a_blank_task_is_no_task(monkeypatch, session, blank):
    monkeypatch.delenv("COPILOT_OPERATOR_NO_DEBUG_LOG", raising=False)
    launch.start_session(session, ["--", blank], 1, True, "Preamble.")
    spec = json.loads(session.spec_file.read_text(encoding="utf-8"))
    assert spec["argv"] == ["copilot", "-i", "Preamble.", "--log-level", "debug"]


def test_a_literal_agent_is_not_an_agent_flag():
    assert launch.has_agent_flag(["--", "--agent=evil"]) is False
    assert launch.has_agent_flag(["--agent=x"]) is True


