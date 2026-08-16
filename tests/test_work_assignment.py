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


# ── the real lookup, not a stub of it ───────────────────────────
#
# The tests above stub `catalog_guid`, so they grade the seam and say nothing
# about the lookup it now shares with `project_handoff_file`. That function
# decides whether a restarting session is told its handoff is waiting, missing,
# or unknowable, and the three answers must not collapse into each other. These
# call the real thing against a real catalog file.
@pytest.fixture
def catalog(tmp_path, monkeypatch):
    """A projects root with a writable catalog, isolated from the machine."""
    root = tmp_path / "projects"
    root.mkdir(parents=True)
    monkeypatch.setattr(op, "projects_root", lambda: root)
    monkeypatch.setattr(op, "project_catalog_path", lambda: root / "catalog.csv")
    return root / "catalog.csv"


def test_an_absent_catalog_is_not_registered_rather_than_unreadable(catalog):
    found = op.catalog_guid(Path.cwd())
    assert found.guid is None
    assert found.undecided is False, (
        "an absent catalog is a settled answer: the project is not registered"
    )


def test_a_registered_checkout_resolves_to_its_guid(catalog, tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch_free_root = str(repo.resolve())
    catalog.write_text(f'"{monkeypatch_free_root}",a-guid\n', encoding="utf-8")
    assert op.catalog_guid(repo).guid == "a-guid"


def test_a_row_that_cannot_be_compared_leaves_the_verdict_undecided(
        catalog, tmp_path, monkeypatch):
    """"No row matched" is only an answer if every row was actually compared.

    Without this, one malformed row turns "I could not tell" into "not
    registered" -- and `handoff_state` turns that into "no handoff is expected
    here" for a session that may well have one waiting.
    """
    catalog.write_text('"\x00not-a-path",a-guid\n', encoding="utf-8")
    found = op.catalog_guid(tmp_path / "repo")
    assert found.guid is None
    assert found.undecided is True, (
        "a row that could not be resolved was counted as a row that did not "
        "match"
    )


def test_an_unreadable_catalog_is_undecided_not_unregistered(
        catalog, tmp_path, monkeypatch):
    catalog.write_text('"/somewhere",a-guid\n', encoding="utf-8")

    def refuse(*args, **kwargs):
        raise OSError(13, "Permission denied")

    monkeypatch.setattr("builtins.open", refuse)
    found = op.catalog_guid(tmp_path / "repo")
    assert found.guid is None
    assert found.undecided is True


def test_the_handoff_path_keeps_the_three_answers_apart(catalog, tmp_path):
    """The wrapper's contract, through the real lookup rather than a stub.

    `project_handoff_file` deliberately makes **no presence probe of its own**.
    It used to, and splitting the lookup out left that probe in place beside
    the one inside `catalog_guid` -- two stats of a file that gets rewritten,
    which disagree in the ordinary `unlink`-then-`replace` window. When they
    disagreed the answer was `None`, and `None` reaches a restarting session as
    "no handoff is expected here".
    """
    assert op.project_handoff_file(tmp_path / "repo") is None

    repo = tmp_path / "repo2"
    repo.mkdir()
    catalog.write_text(f'"{repo.resolve()}",a-guid\n', encoding="utf-8")
    got = op.project_handoff_file(repo, "seat-one")
    assert got is not None and got.name == "seat-one.md"
    assert got.parent.name == "handoff"


def test_an_undecided_catalog_reaches_the_caller_as_unreadable(
        catalog, tmp_path, monkeypatch):
    catalog.write_text('"/somewhere",a-guid\n', encoding="utf-8")
    monkeypatch.setattr(op, "catalog_guid",
                        lambda cwd: op.CatalogLookup(None, undecided=True))
    assert op.project_handoff_file(tmp_path / "repo") is op.CATALOG_UNREADABLE


def test_the_handoff_path_probes_the_catalog_exactly_once(
        catalog, tmp_path, monkeypatch):
    """The race, made deterministic.

    Two probes of a file that can be rewritten can disagree, and when they did
    the answer was `None` -- which `handoff_state` turns into "no handoff is
    expected here" for a session that may have one waiting. Rather than test a
    timing window, this hands the code a probe that answers `True` and then
    `False`, which is what a catalog being replaced looks like from inside.

    One probe cannot disagree with itself, so the count is half the assertion:
    without it, a future second probe that happens to agree in this test would
    pass while reintroducing the window.
    """
    catalog.write_text('"/somewhere",a-guid\n', encoding="utf-8")
    answers = iter([True, False, False, False])
    calls = []

    def flapping(path):
        calls.append(path)
        return next(answers, False)

    monkeypatch.setattr(op, "file_present", flapping)
    # The file is gone by the time `open` runs, which is the second half of the
    # same window: a probe that said "there" and a read that finds nothing.
    catalog.unlink()

    got = op.project_handoff_file(tmp_path / "repo", "seat-one")
    assert got is op.CATALOG_UNREADABLE, (
        "a catalog that vanished mid-lookup was reported as 'not registered', "
        "which reaches a restarting session as 'no handoff is expected here'"
    )
    assert len(calls) == 1, (
        f"the catalog was probed {len(calls)} times; two probes of a file that "
        f"gets rewritten can disagree, and this path must not guess"
    )


def test_an_undecided_lookup_is_not_reported_as_having_no_work(
        tmp_path, monkeypatch, said):
    """The seam's half of the same distinction.

    An unreadable catalog must not reach the agent as "you have no assignment",
    which is exactly what an empty queue looks like.
    """
    class Store:
        def db_path(self, project):  # pragma: no cover - must not be reached
            raise AssertionError("opened a database for an unsettled project")

    monkeypatch.setattr(op, "_SESSION_STORE", Store())
    monkeypatch.setattr(op, "catalog_guid",
                        lambda cwd: op.CatalogLookup(None, undecided=True))
    assert op._loop_work_db(tmp_path) is None
    assert any("not the same as having no work" in line for line in said), (
        f"an unsettled catalog was reported as an absence of work: {said!r}")
