"""Work assignment, and the difference between *off* and *empty*.

`_SESSION_STORE` is injected by whoever starts the loop, and **nothing in this
repository does**: the entry point that would call `set_session_store` is still
in the frozen `copilot-tools`. So assignment is switched off on every live seat
today, and the way it is switched off is by `session_store()` returning `None`
-- which reaches the agent as "you have no assignment", which is exactly what an
agent with an empty queue sees.

That is this project's signature failure with a different subject: a signal
indistinguishable from its absence. It has already been paid for here once, in
the same subsystem -- both call sites referred to `operator_session` as a bare
name that nothing imported, so every call raised `NameError`, both handlers
caught it, and the whole feature answered "no assignment" for its entire life
while `test_kernel_boundary`'s import scan read clean.

These tests do not wire the store, because the kernel is the wrong place to do
it. They pin the one thing the kernel can be responsible for: that its silence
is not silent.
"""
from __future__ import annotations

import pytest

import supervisor


@pytest.fixture
def said(monkeypatch):
    """Everything the supervisor logged during the test."""
    lines: list[str] = []
    monkeypatch.setattr(supervisor, "log", lines.append)
    return lines


def test_an_unconfigured_store_says_so_rather_than_answering_no_work(
        tmp_path, monkeypatch, said):
    monkeypatch.setattr(supervisor, "_SESSION_STORE", None)
    assert supervisor._loop_work_db(tmp_path) is None
    assert any("assignment is off" in line for line in said), (
        f"nothing distinguished an unconfigured store from an empty queue; "
        f"the supervisor said {said!r}")


def test_a_configured_store_does_not_claim_assignment_is_off(
        tmp_path, monkeypatch, said):
    """The negative control. Without it the line could be emitted on every
    launch, and a message that is always printed carries no information at all.

    The store is a stub that cannot resolve anything, so this still returns
    `None` -- the point is only that it does not blame a *missing* store for it.
    """
    class Store:
        def db_path(self, _):
            raise RuntimeError("not registered")

    monkeypatch.setattr(supervisor, "_SESSION_STORE", Store())
    assert supervisor._loop_work_db(tmp_path) is None
    assert not any("assignment is off" in line for line in said), said


def test_no_store_costs_the_agent_a_hint_and_never_a_session(monkeypatch, said):
    """The kernel's half of the contract: it works without a store.

    A missing assignment must cost the agent a hint and must not cost it a
    session, so this returns `None` rather than raising -- and that is why the
    log line above has to exist.
    """
    monkeypatch.setattr(supervisor, "_SESSION_STORE", None)
    assert supervisor._loop_start_session(None, instance=None, session_num=1) is None
