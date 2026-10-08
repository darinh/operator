"""What a caller may do to which operator, and where a child starts."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

import custody
import operators
from operator_cli import family
from test_handoff import _seat
from test_lifecycle import _repo_with_worktree

PERSON = custody.Human(4321)


def _agent(record):
    return custody.Agent(record, "7", 500)


def _line(tmp_path, *names):
    """Operators each started by the one before, the first by a person."""
    made, parent = [], operators.HUMAN
    for name in names:
        made.append(operators.create(name, tmp_path, parent=parent))
        parent = made[-1].id
    return made


def test_a_person_starts_where_they_stand(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert family.place(PERSON, None, True, None) == Path.cwd()


def test_a_child_starts_where_its_parent_works_not_where_the_shell_stands(
        tmp_path, monkeypatch):
    (tmp_path / "lead").mkdir()
    lead = operators.create("lead", tmp_path / "lead")
    monkeypatch.chdir(tmp_path)
    assert family.place(_agent(lead), "scout", False, None) == Path(lead.cwd)


@pytest.mark.parametrize("name, attach, said", [
    (None, False, "an operator must name the child it starts: "
                  'operator start NAME "..."'),
    ("scout", True, "only a person can attach, so an operator cannot pass --attach"),
])
def test_an_agent_names_its_child_and_does_not_attach(tmp_path, capsys, name, attach,
                                                      said):
    lead = operators.create("lead", tmp_path)
    assert family.place(_agent(lead), name, attach, None) == 2
    assert capsys.readouterr().err == f"operator start: {said}\n"


def test_dir_takes_any_checkout_of_the_same_repository(tmp_path, monkeypatch):
    primary, linked = _repo_with_worktree(tmp_path)
    monkeypatch.chdir(primary)
    assert family.place(PERSON, "b", False, str(linked)) == linked.resolve()
    assert family.dir_problem(primary.resolve(), linked) is None


def test_dir_refuses_a_plain_directory_and_prints_the_command_that_fixes_it(tmp_path):
    primary, _ = _repo_with_worktree(tmp_path)
    wanted = (tmp_path / "fresh").resolve()
    problem = family.dir_problem(wanted, primary)
    assert problem == (f"{wanted} is not a worktree of this project.\n"
                       "Create it first, then run this command again:\n"
                       f'    git -C "{primary}" worktree add "{wanted}"')
    subprocess.run(problem.splitlines()[-1].strip(), shell=True, check=True,
                   capture_output=True)
    assert family.dir_problem(wanted, primary) is None


def test_dir_refuses_a_worktree_of_another_repository(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    primary, _ = _repo_with_worktree(tmp_path / "a")
    _, foreign = _repo_with_worktree(tmp_path / "b")
    assert family.dir_problem(foreign.resolve(), primary).endswith(
        f'worktree add "{foreign.resolve()}"')


def test_dir_outside_git_says_there_is_no_worktree_to_make(tmp_path):
    (tmp_path / "plain").mkdir()
    assert family.dir_problem(tmp_path, tmp_path / "plain") == (
        f"{tmp_path / 'plain'} is not in a git repository, "
        "so it has no worktrees to start in.")


def test_a_person_has_no_caps(tmp_path, monkeypatch):
    monkeypatch.setenv("OPERATOR_MAX_DEPTH", "1")
    monkeypatch.setenv("OPERATOR_MAX_CHILDREN", "1")
    _line(tmp_path, "a", "b")
    assert family.cap_problem(PERSON, new=True) is None


def test_the_fifth_running_child_waits_until_one_stops_or_the_cap_rises(
        tmp_path, monkeypatch):
    import supervisor_control
    [lead] = _line(tmp_path, "lead")
    kids = [operators.create(f"kid{n}", tmp_path, parent=lead.id) for n in range(4)]
    running = list(kids)
    monkeypatch.setattr(supervisor_control, "active_instances",
                        lambda: [kid.instance() for kid in running])
    assert family.cap_problem(_agent(lead), new=True) == (
        "lead already runs 4 children, and OPERATOR_MAX_CHILDREN allows 4. "
        "Stop one first.")
    assert family.cap_problem(_agent(lead), new=False) is not None
    running.pop()
    assert family.cap_problem(_agent(lead), new=True) is None
    running = list(kids)
    monkeypatch.setenv("OPERATOR_MAX_CHILDREN", "5")
    assert family.cap_problem(_agent(lead), new=True) is None


def test_a_child_whose_supervisor_is_still_starting_takes_its_place(tmp_path, monkeypatch):
    """A launch that has not published its pid can still come up, so the next
    start counts it, or a slow supervisor lets one child past the cap."""
    import os

    import supervisor_control
    from supervisor_records import _record_supervisor_starting
    [lead] = _line(tmp_path, "lead")
    kids = [operators.create(f"kid{n}", tmp_path, parent=lead.id) for n in range(4)]
    monkeypatch.setattr(supervisor_control, "active_instances",
                        lambda: [kid.instance() for kid in kids[:3]])
    assert family.cap_problem(_agent(lead), new=True) is None
    _record_supervisor_starting(kids[3].instance(), os.getpid())
    assert family.cap_problem(_agent(lead), new=True) == (
        "lead already runs 4 children, and OPERATOR_MAX_CHILDREN allows 4. "
        "Stop one first.")


def test_a_fourth_level_is_refused_until_the_depth_rises(tmp_path):
    a, b, c = _line(tmp_path, "a", "b", "c")
    assert family.cap_problem(_agent(b), new=True) is None
    assert family.cap_problem(_agent(c), new=True) == (
        "a child of c would be 4 levels deep, and OPERATOR_MAX_DEPTH allows 3.")
    # A child that already exists is not made any deeper by restarting it.
    assert family.cap_problem(_agent(c), new=False) is None


def test_the_depth_cap_follows_its_variable(tmp_path, monkeypatch):
    *_, c = _line(tmp_path, "a", "b", "c")
    monkeypatch.setenv("OPERATOR_MAX_DEPTH", "4")
    assert family.cap_problem(_agent(c), new=True) is None


def test_an_operator_acts_only_on_its_own_children(tmp_path, capsys):
    lead, scout, deep = _line(tmp_path, "lead", "scout", "deep")
    other = operators.create("other", tmp_path)
    allowed = {(who.name, target.name)
               for who in (lead, scout, deep)
               for target in (lead, scout, deep, other)
               if not family.not_yours("stop", _agent(who), target)}
    assert allowed == {("lead", "scout"), ("scout", "deep")}
    assert not any(family.not_yours("stop", PERSON, target)
                   for target in (lead, scout, deep, other))
    assert capsys.readouterr().err.splitlines()[0] == (
        "operator stop: lead is not your child. "
        "An operator may stop only the operators it started.")


def test_only_a_person_passes_person_only(tmp_path, monkeypatch, capsys):
    import process_tree
    monkeypatch.setattr(process_tree, "ancestry", lambda pid: [4321])
    assert family.person_only("rename") == 0
    _seat(monkeypatch, operators.create("lead", tmp_path))
    assert family.person_only("rename") == 2
    monkeypatch.setattr(process_tree, "ancestry", lambda pid: None)
    assert family.person_only("rename") == 1
    assert capsys.readouterr().err.splitlines() == [
        "operator rename: only a person can do this, not an operator",
        "operator rename: could not read the process table",
    ]
