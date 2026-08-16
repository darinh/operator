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

from pathlib import Path

import pytest

import op


@pytest.fixture
def said(monkeypatch):
    """Everything the supervisor logged during the test."""
    lines: list[str] = []
    monkeypatch.setattr(op, "log", lines.append)
    return lines


def test_an_unconfigured_store_says_so_rather_than_answering_no_work(
        tmp_path, monkeypatch, said):
    monkeypatch.setattr(op, "_SESSION_STORE", None)
    assert op._loop_work_db(tmp_path) is None
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

    monkeypatch.setattr(op, "_SESSION_STORE", Store())
    assert op._loop_work_db(tmp_path) is None
    assert not any("assignment is off" in line for line in said), said


def test_no_store_costs_the_agent_a_hint_and_never_a_session(monkeypatch, said):
    """The kernel's half of the contract: it works without a store.

    A missing assignment must cost the agent a hint and must not cost it a
    session, so this returns `None` rather than raising -- and that is why the
    log line above has to exist.
    """
    monkeypatch.setattr(op, "_SESSION_STORE", None)
    assert op._loop_start_session(None, instance=None, session_num=1) is None


def test_a_resolvable_project_actually_reaches_the_store(monkeypatch, said):
    """The test the two above could not be: it gets past the catalog lookup.

    Both of them stop at "returns None", and every route through
    `_loop_work_db` returns None -- including the one where the lookup itself
    raises and `except Exception` swallows it. That is not hypothetical here.
    `catalog_guid` was called by name in `supervisor.py` for the whole life of
    the extraction and **was defined nowhere**, so a configured store reached a
    `NameError`, the handler caught it, and the function answered `None` --
    which arrives at the agent as "you have no assignment". The negative
    control above passed throughout, because its stub raises and it cannot tell
    a stub that raised from a stub that was never called.

    So this one asserts the store was *reached*, with the lookup stubbed to
    succeed. It is the only assertion here that fails if the lookup is broken.
    """
    reached = []

    class Store:
        def db_path(self, project):
            reached.append(project)
            return project / "work.db"

    monkeypatch.setattr(op, "_SESSION_STORE", Store())
    monkeypatch.setattr(op, "catalog_guid",
                        lambda cwd: op.CatalogLookup("a-guid"))
    result = op._loop_work_db(Path("/anywhere"))
    assert reached, (
        f"the work store was never asked for a database. The lookup failed "
        f"and `except Exception` turned it into 'no assignment'. "
        f"Supervisor said: {said!r}"
    )
    assert result == reached[0] / "work.db"


def test_the_catalog_lookup_the_seam_depends_on_exists():
    """`catalog_guid` is called through a bare module global, so it is pinned.

    An import scan cannot see a name that is used but never imported, which is
    how this subsystem has now been broken twice -- first `operator_session`,
    then `catalog_guid`. `test_kernel_boundary.py` grades undefined globals
    across the whole kernel now; this names the one that was missing.
    """
    assert callable(op.catalog_guid)
    assert op.catalog_guid.__module__ == "paths"
    assert op.CatalogLookup(None).guid is None
    assert op.CatalogLookup("g").guid == "g"
