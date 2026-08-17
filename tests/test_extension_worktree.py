"""The worktree extensions, against real repositories rather than fakes.

`git worktree` behaviour is the thing under test, so the tests make real
worktrees. A fake `subprocess.run` here would be asserting that this repository
agrees with itself about a porcelain format it does not control -- and the
format is exactly what would change under it.

The one deliberate simulation is the family of half-finished states. A genuine
stuck merge is created for real, because that is the case the guard exists for
and the case a mistake would be most expensive in. Its siblings -- rebase,
cherry-pick, revert, bisect -- are asserted through the markers git leaves,
because standing up five genuinely interrupted git operations costs seconds per
test and proves the same one thing: that the marker is looked for in the right
directory.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from operator_extensions import (activation, gitfacts, worktree_guard,
                                 worktree_janitor)

pytestmark = pytest.mark.usefixtures("_extension_home")


@pytest.fixture
def _extension_home(tmp_path, monkeypatch):
    """Point the extensions' state and config at a temporary directory.

    `activation.operator_home()` reads the environment on every call rather
    than at import, so this reaches it. That is not incidental -- three tests
    elsewhere in this repository read the developer's real `~/.operator`
    because a path was captured at import time, and this fixture would be inert
    in exactly the same way.
    """
    home = tmp_path / "operator-home"
    home.mkdir()
    monkeypatch.setenv("COPILOT_OPERATOR_HOME", str(home))
    assert activation.operator_home() == home
    return home


def enable(home: Path, **entries) -> None:
    (home / activation.CONFIG_NAME).write_text(json.dumps(entries),
                                               encoding="utf-8")


def git(root, *args, check=True):
    done = subprocess.run(["git", "-C", str(root), *args],
                          capture_output=True, text=True, timeout=30)
    if check and done.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} failed: {done.stderr}")
    return done


@pytest.fixture
def repo(tmp_path):
    """A repository with one commit on `main` and a usable identity."""
    root = tmp_path / "project"
    root.mkdir()
    git(root, "init", "-b", "main")
    git(root, "config", "user.email", "test@example.invalid")
    git(root, "config", "user.name", "Test")
    git(root, "config", "commit.gpgsign", "false")
    (root / "file.txt").write_text("base\n", encoding="utf-8")
    git(root, "add", "file.txt")
    git(root, "commit", "-m", "base")
    return root


# ── gitfacts: the porcelain parser ──────────────────────────────

PORCELAIN = """\
worktree /repos/project
HEAD aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
branch refs/heads/main

worktree /repos/project/.worktrees/feature
HEAD bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb
branch refs/heads/feature

worktree /repos/project/.worktrees/gone
HEAD cccccccccccccccccccccccccccccccccccccccc
detached
prunable gitdir file points to non-existent location

worktree /repos/project/.worktrees/held
HEAD dddddddddddddddddddddddddddddddddddddddd
branch refs/heads/held
locked human is using it
"""


def test_the_parser_reads_every_group_and_shortens_the_branch():
    found = gitfacts.parse_worktrees(PORCELAIN)
    assert [w.path for w in found] == [
        "/repos/project",
        "/repos/project/.worktrees/feature",
        "/repos/project/.worktrees/gone",
        "/repos/project/.worktrees/held",
    ]
    assert found[0].branch == "main", "refs/heads/ should be stripped"
    assert found[2].detached is True and found[2].prunable is True
    assert found[3].locked is True and found[3].branch == "held"


def test_a_group_with_no_worktree_line_is_dropped_not_merged():
    """Attributes must never leak onto the neighbouring checkout.

    A janitor that merged a stray group into the previous one would propose
    removing a directory on the strength of another directory's state.
    """
    found = gitfacts.parse_worktrees(
        "HEAD aaaa\nbranch refs/heads/orphan\n\n"
        "worktree /repos/real\nbranch refs/heads/main\n")
    assert [w.path for w in found] == ["/repos/real"]
    assert found[0].branch == "main"


def test_the_parser_survives_empty_and_junk_input():
    assert gitfacts.parse_worktrees("") == []
    assert gitfacts.parse_worktrees("\n\n\n") == []
    assert gitfacts.parse_worktrees("nonsense without a key") == []


def test_a_spent_budget_stops_a_command_rather_than_running_it_briefly(
        repo, monkeypatch):
    """Zero remaining must mean "do not ask", not "ask with a zero timeout".

    A zero timeout is a `TimeoutExpired` a few milliseconds later, which is a
    subprocess spawned anyway -- and the whole point of the budget is that the
    worker replies before the host kills it.

    Asserted by making `subprocess.run` fail the test if it is reached, through
    a *public* helper that takes a budget. The first version of this called the
    private `_run` with a literal `0.0`, which tested the floor inside `_run`
    and would have passed unchanged if every caller had stopped passing the
    budget down at all -- which is the defect a reviewer then found in
    `worktree_janitor._repos`.
    """
    def explode(*args, **kwargs):
        raise AssertionError("a spent budget must not spawn git")

    monkeypatch.setattr(gitfacts.subprocess, "run", explode)
    spent = gitfacts.Budget(seconds=0.0)
    assert spent.spent() is True
    assert gitfacts.is_repo(repo, spent) is False
    assert gitfacts.worktrees(repo, spent) == []
    assert gitfacts.has_changes(repo, spent) is None
    assert gitfacts.is_merged(repo, "main", "main", spent) is False


def test_repository_discovery_also_respects_the_budget(repo, _extension_home,
                                                       monkeypatch):
    """Discovery ran outside the budget entirely, which made a root with many
    children a reliable way to overrun the worker deadline and quarantine this
    extension for the life of the fleet host."""
    enable(_extension_home,
           **{worktree_janitor.NAME: {"enabled": True,
                                      "roots": [str(repo.parent)]}})

    def explode(*args, **kwargs):
        raise AssertionError("discovery must not spawn git on a spent budget")

    monkeypatch.setattr(gitfacts.subprocess, "run", explode)
    monkeypatch.setattr(gitfacts, "SCAN_BUDGET", 0.0)
    assert worktree_janitor.propose_work() is None


def test_gitfacts_never_raises_on_a_directory_that_is_not_a_repository(tmp_path):
    assert gitfacts.is_repo(tmp_path / "nope") is False
    assert gitfacts.git_dir(tmp_path / "nope") is None
    assert gitfacts.unfinished(tmp_path / "nope") == ""
    assert gitfacts.worktrees(tmp_path / "nope") == []
    assert gitfacts.has_changes(tmp_path / "nope") is None


def test_has_changes_is_tri_state(repo):
    assert gitfacts.has_changes(repo) is False
    (repo / "file.txt").write_text("dirty\n", encoding="utf-8")
    assert gitfacts.has_changes(repo) is True


def test_a_file_hidden_from_status_makes_the_answer_unknown(repo):
    """`assume-unchanged` hides a real modification from `git status`.

    A reviewer lost a file to this: status reported clean, the janitor said
    removal "would lose nothing", and `git worktree remove` -- which shares the
    blind spot -- deleted the modification. Unknown vetoes the proposal.
    """
    git(repo, "update-index", "--assume-unchanged", "file.txt")
    (repo / "file.txt").write_text("secretly modified\n", encoding="utf-8")
    assert gitfacts.has_changes(repo) is None, (
        "a masked index entry means status cannot be trusted")


def test_skip_worktree_is_treated_the_same_way(repo):
    git(repo, "update-index", "--skip-worktree", "file.txt")
    assert gitfacts.has_changes(repo) is None


def test_untracked_work_is_seen_even_when_the_repo_hides_it(repo):
    """`status.showUntrackedFiles=no` empties porcelain output.

    A reviewer pointed out that the janitor's own "an afternoon of work" test
    would have passed against a repository configured this way while the
    proposal said removal would lose nothing. `--untracked-files=all` is passed
    explicitly so the repository cannot answer for us.
    """
    git(repo, "config", "status.showUntrackedFiles", "no")
    (repo / "wip.txt").write_text("an afternoon of work\n", encoding="utf-8")
    assert gitfacts.has_changes(repo) is True


def test_an_inherited_git_dir_cannot_redirect_the_answer(repo, tmp_path,
                                                         monkeypatch):
    """An absolute `GIT_DIR` overrides `-C`, so every answer would describe
    the wrong repository -- a guard refusing a clean workdir because something
    else is mid-merge."""
    other = tmp_path / "elsewhere"
    other.mkdir()
    git(other, "init", "-b", "main")
    git(other, "config", "user.email", "t@example.invalid")
    git(other, "config", "user.name", "T")
    (other / "o.txt").write_text("o\n", encoding="utf-8")
    git(other, "add", "o.txt")
    git(other, "commit", "-m", "other")

    monkeypatch.setenv("GIT_DIR", str(gitfacts.git_dir(other)))
    where = gitfacts.git_dir(repo)
    assert where is not None
    assert where.parent.resolve() == repo.resolve(), (
        f"GIT_DIR redirected the answer to {where}")


def test_the_janitor_will_not_propose_a_worktree_whose_index_is_masked(
        repo, _extension_home):
    enable(_extension_home,
           **{worktree_janitor.NAME: {"enabled": True, "roots": [str(repo)]}})
    path = add_worktree(repo, "masked", "masked")
    git(path, "update-index", "--assume-unchanged", "file.txt")
    (path / "file.txt").write_text("hidden work\n", encoding="utf-8")
    assert worktree_janitor.propose_work() is None


def test_is_merged_says_no_when_it_cannot_ask(repo):
    """The janitor proposes deletion on this answer, so unknown must read no."""
    assert gitfacts.is_merged(repo, "no-such-branch", "main") is False
    assert gitfacts.is_merged(repo, "main", "no-such-integration") is False
    assert gitfacts.is_merged(repo, "", "main") is False


# ── the guard ───────────────────────────────────────────────────

def test_the_guard_has_no_opinion_until_a_human_enables_it(repo, _extension_home):
    """Installing must not change how the fleet behaves."""
    conflicted(repo)
    assert worktree_guard.admit_launch(
        instance="seat", session=1, workdir=str(repo)) is None


def conflicted(root) -> None:
    """Leave `root` in a genuine, unresolved merge conflict."""
    git(root, "checkout", "-b", "other")
    (root / "file.txt").write_text("theirs\n", encoding="utf-8")
    git(root, "commit", "-am", "theirs")
    git(root, "checkout", "main")
    (root / "file.txt").write_text("ours\n", encoding="utf-8")
    git(root, "commit", "-am", "ours")
    done = git(root, "merge", "other", check=False)
    assert done.returncode != 0, "the merge was supposed to conflict"


def test_the_guard_refuses_a_launch_into_a_real_unfinished_merge(
        repo, _extension_home):
    enable(_extension_home, **{worktree_guard.NAME: {"enabled": True}})
    conflicted(repo)
    assert gitfacts.unfinished(repo) == "merge"

    answer = worktree_guard.admit_launch(
        instance="seat", session=1, workdir=str(repo))
    assert answer["admit"] is False
    assert answer["state"] == "merge"
    assert "unfinished merge" in answer["reason"]


def test_the_guard_admits_a_clean_repository(repo, _extension_home):
    enable(_extension_home, **{worktree_guard.NAME: {"enabled": True}})
    assert worktree_guard.admit_launch(
        instance="seat", session=1, workdir=str(repo)) is None


@pytest.mark.parametrize("state,marker", list(gitfacts.IN_PROGRESS))
def test_every_half_finished_state_is_looked_for_in_the_worktree_git_dir(
        repo, _extension_home, state, marker):
    """The marker must be read from the per-worktree git directory.

    `--absolute-git-dir` gives the linked worktree's own directory, so a rebase
    in one worktree is not reported against another. Writing the marker into
    what `git_dir` returns and expecting the guard to see it is what pins that.
    """
    enable(_extension_home, **{worktree_guard.NAME: {"enabled": True}})
    where = gitfacts.git_dir(repo)
    assert where is not None
    target = where / marker
    if marker.startswith("rebase-"):
        target.mkdir()
    else:
        target.write_text("x\n", encoding="utf-8")

    answer = worktree_guard.admit_launch(
        instance="seat", session=1, workdir=str(repo))
    assert answer["admit"] is False
    assert answer["state"] == state


@pytest.mark.parametrize("workdir", [None, "", "   ", 42, {"path": "x"}])
def test_the_guard_admits_when_the_workdir_is_not_a_usable_string(
        _extension_home, workdir):
    enable(_extension_home, **{worktree_guard.NAME: {"enabled": True}})
    assert worktree_guard.admit_launch(
        instance="seat", session=1, workdir=workdir) is None


def test_the_guard_admits_a_directory_that_is_not_a_repository(
        tmp_path, _extension_home):
    enable(_extension_home, **{worktree_guard.NAME: {"enabled": True}})
    plain = tmp_path / "not-a-repo"
    plain.mkdir()
    assert worktree_guard.admit_launch(
        instance="seat", session=1, workdir=str(plain)) is None


# ── the janitor ─────────────────────────────────────────────────

def add_worktree(repo: Path, name: str, branch: str) -> Path:
    path = repo / ".worktrees" / name
    git(repo, "worktree", "add", "-b", branch, str(path))
    return path


def test_the_janitor_proposes_a_merged_worktree_once(repo, _extension_home):
    enable(_extension_home,
           **{worktree_janitor.NAME: {"enabled": True, "roots": [str(repo)],
                                      "integration": "main"}})
    add_worktree(repo, "landed", "landed")

    first = worktree_janitor.propose_work()
    assert first and len(first) == 1
    assert "landed" in first[0]["title"]
    assert "merged into main" in first[0]["title"]

    assert worktree_janitor.propose_work() is None, (
        "a proposal repeated every tick fills a 4 MB queue nobody drains")


def test_the_janitor_proposes_again_after_the_finding_clears_and_returns(
        repo, _extension_home):
    enable(_extension_home,
           **{worktree_janitor.NAME: {"enabled": True, "roots": [str(repo)]}})
    path = add_worktree(repo, "landed", "landed")
    assert worktree_janitor.propose_work()

    git(repo, "worktree", "remove", str(path))
    assert worktree_janitor.propose_work() is None

    add_worktree(repo, "landed", "landed2")
    again = worktree_janitor.propose_work()
    assert again, "memory is of what the scan saw, not a permanent suppression"


def test_the_janitor_will_not_propose_removing_a_worktree_with_changes(
        repo, _extension_home):
    enable(_extension_home,
           **{worktree_janitor.NAME: {"enabled": True, "roots": [str(repo)]}})
    path = add_worktree(repo, "busy", "busy")
    (path / "wip.txt").write_text("an afternoon of work\n", encoding="utf-8")
    assert worktree_janitor.propose_work() is None


def test_the_janitor_leaves_a_locked_worktree_alone(repo, _extension_home):
    """A lock is a human saying so in git's own vocabulary."""
    enable(_extension_home,
           **{worktree_janitor.NAME: {"enabled": True, "roots": [str(repo)]}})
    path = add_worktree(repo, "held", "held")
    git(repo, "worktree", "lock", str(path))
    assert worktree_janitor.propose_work() is None


def test_the_janitor_proposes_an_unfinished_operation_the_guard_would_refuse(
        repo, _extension_home):
    """The two extensions have to agree, or a seat sits refused with no reason
    anywhere a person looks."""
    enable(_extension_home,
           **{worktree_janitor.NAME: {"enabled": True, "roots": [str(repo)]}})
    path = add_worktree(repo, "stuck", "stuck")
    where = gitfacts.git_dir(path)
    (where / "MERGE_HEAD").write_text("x\n", encoding="utf-8")

    proposals = worktree_janitor.propose_work()
    assert proposals and "unfinished merge" in proposals[0]["title"]


def test_the_janitor_does_not_propose_the_main_checkout(repo, _extension_home):
    """The first entry from `git worktree list` is the repository itself."""
    enable(_extension_home,
           **{worktree_janitor.NAME: {"enabled": True, "roots": [str(repo)]}})
    assert worktree_janitor.propose_work() is None


def test_the_janitor_reports_an_unfinished_operation_in_the_main_checkout(
        repo, _extension_home):
    """The seat's workdir is usually the main checkout, so this is the case
    the guard/janitor pairing exists for -- and it was the one being skipped.

    Removal is still never proposed for it; only the condition a human has to
    resolve.
    """
    enable(_extension_home,
           **{worktree_janitor.NAME: {"enabled": True, "roots": [str(repo)]}})
    conflicted(repo)

    proposals = worktree_janitor.propose_work()
    assert proposals, "the guard will refuse this checkout forever in silence"
    assert "main checkout has an unfinished merge" in proposals[0]["title"]
    assert all("remove" not in item["title"] for item in proposals)


def test_a_root_that_is_a_directory_of_repositories_is_scanned(
        repo, _extension_home):
    enable(_extension_home,
           **{worktree_janitor.NAME: {"enabled": True,
                                      "roots": [str(repo.parent)]}})
    add_worktree(repo, "landed", "landed")
    proposals = worktree_janitor.propose_work()
    assert proposals and "landed" in proposals[0]["title"]


def test_the_janitor_survives_roots_that_are_nonsense(_extension_home, tmp_path):
    enable(_extension_home,
           **{worktree_janitor.NAME: {
               "enabled": True,
               "roots": [str(tmp_path / "missing"), "", 7, None]}})
    assert worktree_janitor.propose_work() is None


def test_every_proposal_has_the_shape_the_fleet_host_accepts(
        repo, _extension_home):
    """`FleetHost._vetted` reports anything else as a BadProposal by name."""
    enable(_extension_home,
           **{worktree_janitor.NAME: {"enabled": True, "roots": [str(repo)]}})
    add_worktree(repo, "landed", "landed")
    proposals = worktree_janitor.propose_work()
    assert isinstance(proposals, list)
    for item in proposals:
        assert isinstance(item, dict)
        assert isinstance(item["title"], str) and item["title"].strip()
        assert isinstance(item["detail"], str)


def test_findings_past_the_cap_are_proposed_on_the_next_call(
        repo, _extension_home):
    """They used to be marked proposed by the call that never returned them.

    The first version remembered everything the scan saw rather than what it
    handed back, so with seven stale worktrees a human was shown five and the
    other two were suppressed forever. A reviewer found it by counting.
    """
    enable(_extension_home,
           **{worktree_janitor.NAME: {"enabled": True, "roots": [str(repo)]}})
    total = worktree_janitor.MAX_PER_CALL + 2
    for n in range(total):
        add_worktree(repo, f"landed{n}", f"landed{n}")

    first = worktree_janitor.propose_work()
    assert len(first) == worktree_janitor.MAX_PER_CALL

    second = worktree_janitor.propose_work()
    assert second is not None, "the findings past the cap were lost"
    assert len(second) == total - worktree_janitor.MAX_PER_CALL

    titles = {item["title"] for item in first + second}
    assert len(titles) == total, "every worktree should be named exactly once"
    assert worktree_janitor.propose_work() is None
