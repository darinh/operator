"""Every commit must be attributable to a non-human author.

Backlog 0013 in the source repository: `git log -S "blanket human approval"`
found two commits, both authored as the owner, both carrying a
`Co-authored-by: Copilot` trailer. The trailer was the only thing separating
them from work he wrote himself. He states he did not write the sentence they
introduced, and an agent later quoted it back to him as his own standing
instruction.

In his own repository that is a forensics problem. On a repository with human
colleagues it is worse: it attributes work to a person who did not do it,
distorts review expectations, and may breach the project's own contribution
policy. And on a wholly agent-owned repository it is worse again in a different
direction, because there is no human present whose absence from the log would
look odd.

So the rule holds regardless of who owns the repository:

- an agent commits under an identity that is obviously not a person;
- `Co-authored-by:` names the human who is accountable for the seat;
- neither is optional, and neither is checked by a person remembering.

This test is the check for *this* repository's own history. The pre-push gate
for managed repositories is a separate mechanism with the same rule.

**What the rule is about, and therefore what it is not about.** It polices a
claim about who *wrote* something. A merge commit that resolved no conflict
wrote nothing: it is a bookkeeping entry recording that two lines of history
were joined, and the person who pressed the button really did perform that act.
Holding such a commit to the rule produced a false positive that could not be
fixed, because the offending merge was already published and rewriting
published history is forbidden here. So a merge contributing no content of its
own is exempt, and the exemption is stated as a predicate over the commit
rather than a list of blessed hashes, so it cannot silently go stale.

Nothing is smuggled in through it. Both parents of an exempt merge are
themselves under the rule, so every commit carrying content was checked on the
way in. A merge that *did* resolve a conflict wrote lines somebody had to
choose, and stays under the rule in full.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import NamedTuple

import pytest

import seat

REPO = Path(__file__).resolve().parent.parent

#: History before the rule existed cannot be retro-attributed, and rewriting
#: published history is forbidden here for the same reason it is forbidden
#: everywhere in this project: somebody may already have it checked out. So the
#: rule applies from the commit that introduced it, and that commit is named.
#: Everything at or after it is checked; everything before is recorded as
#: predating the rule rather than quietly excluded.
RULE_FROM = "477f85e488b6c6d4384d15d17c2546d4b2ef7b5b"

def _git(*args: str, repo: Path = REPO) -> str:
    return subprocess.run(("git",) + args, cwd=repo, capture_output=True,
                          text=True, encoding="utf-8", errors="replace").stdout


class Commit(NamedTuple):
    sha: str
    name: str
    email: str
    parents: "tuple[str, ...]"
    body: str


def commits_under_the_rule(repo: Path = REPO,
                           since: str = RULE_FROM) -> "list[Commit]":
    """Commits at or after `RULE_FROM`, oldest first.

    A reachability range rather than a date range: dates are attacker- and
    rebase-controlled, and a commit can carry any timestamp it likes. It is
    deliberately *not* `--ancestry-path`. That flag keeps only commits on a
    path from the boundary to `HEAD`, which drops a side branch merged in
    later -- so a human-authored commit could be merged in and never appear
    here at all. Set subtraction by reachability has no such gap.
    """
    raw = _git("log", "--format=%H%x00%an%x00%ae%x00%P%x00%B%x00%x00",
               f"{since}..HEAD", repo=repo)
    out = []
    for record in raw.split("\x00\x00"):
        record = record.strip("\n")
        if not record:
            continue
        parts = record.split("\x00")
        if len(parts) >= 5:
            out.append(Commit(parts[0], parts[1], parts[2],
                              tuple(parts[3].split()), parts[4]))
    return out


def own_content(sha: str, repo: Path = REPO) -> str:
    """What a commit contributed that no parent of it already had.

    `--cc` against a merge shows only the hunks differing from *every* parent,
    so an ordinary merge yields nothing at all while a conflict resolution
    yields the lines somebody actually had to write. Against a single-parent
    commit it is the plain diff.
    """
    return _git("diff-tree", "--cc", "--no-commit-id", sha, repo=repo).strip()


def records_only_a_merge(commit: Commit, repo: Path = REPO) -> bool:
    """True for a merge that contributed no content of its own.

    The rule polices a claim about *who wrote something*. A merge that resolved
    no conflict wrote nothing, so it contains no such claim to be false, and
    the person who pressed the button did perform the one act it records. Both
    sides are separately under the rule, so nothing reaches the history through
    this door that was not already checked on the way in.

    Two parents are required as well as an empty diff, because an empty
    single-parent commit also carries no content and is not a merge. This is
    the narrowest statement that admits the honest case and nothing else.
    """
    return len(commit.parents) >= 2 and not own_content(commit.sha, repo=repo)


def commits_making_an_authorship_claim(repo: Path = REPO) -> "list[Commit]":
    return [c for c in commits_under_the_rule(repo=repo)
            if not records_only_a_merge(c, repo=repo)]


AGENT = "kernel (agent) <darin+agent-kernel@users.noreply.github.com>"
PERSON = "Darin Hoover <darinh@gmail.com>"


class _History:
    """A throwaway repository whose every commit author is stated, not implied.

    Exercising the exemption against this project's own log would only restate
    what that log happens to contain today. The two cases that decide whether
    the predicate is narrow enough -- a merge that resolved a conflict, and an
    empty commit that is not a merge -- are not in it, and a predicate tested
    only on history that cannot produce its failure case is untested.
    """

    def __init__(self, root: Path) -> None:
        self.root = root
        root.mkdir(parents=True, exist_ok=True)
        for args in (("init", "-q", "-b", "main"),
                     ("config", "user.name", "seed (agent)"),
                     ("config", "user.email", "seed@agents.invalid"),
                     ("config", "commit.gpgsign", "false")):
            self.git(*args)

    def git(self, *args: str) -> str:
        return _git(*args, repo=self.root)

    def head(self) -> str:
        return self.git("rev-parse", "HEAD").strip()

    def commit(self, message: str, *, author: str, text: "str | None" = None,
               file: str = "f.txt", empty: bool = False) -> str:
        if text is not None:
            (self.root / file).write_text(text, encoding="utf-8")
            self.git("add", file)
        args = ["commit", "-q", "-m", message, f"--author={author}"]
        if empty:
            args.append("--allow-empty")
        self.git(*args)
        return self.head()

    def merge(self, other: str, *, author: str,
              resolve_to: "str | None" = None, file: str = "f.txt") -> str:
        self.git("merge", "--no-ff", "--no-commit", other)
        if resolve_to is not None:
            (self.root / file).write_text(resolve_to, encoding="utf-8")
            self.git("add", file)
        self.git("commit", "-q", "--no-edit", f"--author={author}")
        return self.head()

    def at(self, sha: str) -> Commit:
        raw = _git("log", "-1", "--format=%H%x00%an%x00%ae%x00%P%x00%B",
                   sha, repo=self.root).split("\x00")
        return Commit(raw[0], raw[1], raw[2], tuple(raw[3].split()), raw[4])


@pytest.fixture
def history(tmp_path) -> _History:
    return _History(tmp_path / "history")


def test_a_merge_that_resolved_nothing_makes_no_authorship_claim(history):
    """The case that forced this exemption: a merge made through the forge.

    GitHub authors the merge commit as whoever pressed the button and adds no
    trailer, so the commit reads as a person having written the branch. It did
    not write anything.
    """
    base = history.commit("base", author=AGENT, text="one\n")
    history.git("checkout", "-q", "-b", "side")
    history.commit("side", author=AGENT, text="one\ntwo\n")
    history.git("checkout", "-q", "main")
    history.commit("main moves", author=AGENT, text="one\n", file="other.txt")
    merge = history.merge("side", author=PERSON)

    assert len(history.at(merge).parents) == 2
    assert own_content(merge, repo=history.root) == ""
    assert records_only_a_merge(history.at(merge), repo=history.root)
    assert not records_only_a_merge(history.at(base), repo=history.root)


def test_a_merge_that_resolved_a_conflict_is_still_an_authorship_claim(history):
    """Choosing between two sides is writing, so the rule still applies.

    This is the hole the exemption would open if it asked only 'is it a merge'.
    """
    history.commit("base", author=AGENT, text="one\n")
    history.git("checkout", "-q", "-b", "side")
    history.commit("side says", author=AGENT, text="side\n")
    history.git("checkout", "-q", "main")
    history.commit("main says", author=AGENT, text="main\n")
    merge = history.merge("side", author=PERSON, resolve_to="resolved\n")

    assert len(history.at(merge).parents) == 2
    assert own_content(merge, repo=history.root) != ""
    assert not records_only_a_merge(history.at(merge), repo=history.root)


def test_an_empty_commit_is_not_exempt_because_it_is_not_a_merge(history):
    """An empty commit also carries no content, and is not a bookkeeping join.

    Without the parent count the predicate would wave through anything that
    happened to produce no diff, which is a different and unearned exemption.
    """
    history.commit("base", author=AGENT, text="one\n")
    empty = history.commit("says nothing", author=PERSON, empty=True)

    assert own_content(empty, repo=history.root) == ""
    assert len(history.at(empty).parents) == 1
    assert not records_only_a_merge(history.at(empty), repo=history.root)


def test_a_squashed_branch_is_still_an_authorship_claim(history):
    """The forge's squash button makes a normal commit carrying all the work."""
    base = history.commit("base", author=AGENT, text="one\n")
    squashed = history.commit("squashed", author=PERSON, text="one\ntwo\n")

    assert not records_only_a_merge(history.at(squashed), repo=history.root)
    assert own_content(squashed, repo=history.root) != "", (
        "it has to be caught for carrying content, not merely for having one "
        "parent, or this passes against a predicate that ignores content")
    claims = [c.sha for c in commits_under_the_rule(repo=history.root,
                                                   since=base)]
    assert squashed in claims
    assert not seat.is_agent_identity(history.at(squashed).name,
                                      history.at(squashed).email)


def test_an_octopus_merge_that_resolved_nothing_is_still_bookkeeping(history):
    """Three parents, not two. The predicate says `>= 2` and means it.

    A reviewer mutated `>= 2` to `== 2` and nothing failed, because no octopus
    exists in this repository's history to notice. `git merge` makes them
    without complaint, so the absence was a gap in the tests rather than a
    property of the world.
    """
    history.commit("base", author=AGENT, text="one\n")
    for side in ("a", "b"):
        history.git("checkout", "-q", "-b", side, "main")
        history.commit(f"side {side}", author=AGENT, text=f"{side}\n",
                       file=f"{side}.txt")
    history.git("checkout", "-q", "main")
    history.git("merge", "--no-ff", "--no-commit", "a", "b")
    history.git("commit", "-q", "--no-edit", f"--author={PERSON}")
    octopus = history.head()

    assert len(history.at(octopus).parents) == 3
    assert own_content(octopus, repo=history.root) == ""
    assert records_only_a_merge(history.at(octopus), repo=history.root)


def test_the_exemption_is_load_bearing_on_this_repository():
    """A predicate that never fires here would pass for the wrong reason.

    If this stops being true the exemption has become dead code, and the two
    rule tests above are no longer proving that it is what lets them pass.
    """
    exempt = [c for c in commits_under_the_rule()
              if records_only_a_merge(c)]
    assert exempt, "no merge under the rule is exempt, so nothing exercises it"


def test_the_parents_of_every_exempt_merge_are_themselves_under_the_rule():
    """The argument for the exemption depends on this, so it is checked.

    An exempt merge is safe only because everything it joined was already
    examined. A merge whose parent predates the rule would import unchecked
    content behind a commit the rule waved through.
    """
    checked = {c.sha for c in commits_under_the_rule()}
    boundary = _git("rev-parse", RULE_FROM).strip()
    for commit in commits_under_the_rule():
        if not records_only_a_merge(commit):
            continue
        outside = [p for p in commit.parents
                   if p not in checked and p != boundary]
        assert outside == [], (
            f"exempt merge {commit.sha[:8]} joins {outside}, which the rule "
            "never examined"
        )


def test_the_rule_boundary_names_a_commit_that_exists():
    """A boundary naming nothing would exclude the whole history silently."""
    assert _git("cat-file", "-t", RULE_FROM).strip() == "commit"


def test_there_is_history_under_the_rule():
    assert len(commits_under_the_rule()) >= 1


def test_there_is_something_left_to_check_after_the_exemption():
    """The exemption must not be able to empty the gate it filters.

    `commits_under_the_rule` has been guarded non-empty since it was written,
    but the two rule tests below now read through a filter, and a filter that
    returns nothing makes both of them pass while examining no commit at all.
    A reviewer killed every mutant in the predicate and then made
    `commits_making_an_authorship_claim` return `[]`; the whole file stayed
    green. This is that hole.
    """
    claims = commits_making_an_authorship_claim()
    assert len(claims) >= 1, (
        "every commit under the rule was filtered out as bookkeeping, so the "
        "two tests below are checking nothing"
    )


def test_no_commit_under_the_rule_is_authored_by_a_human_identity():
    offenders = [
        f"{c.sha[:8]} {c.name} <{c.email}>"
        for c in commits_making_an_authorship_claim()
        if not seat.is_agent_identity(c.name, c.email)
    ]
    assert offenders == [], (
        "these commits are authored under an identity that reads as a person:\n  "
        + "\n  ".join(offenders)
        + "\n\nA seat commits as itself and names its accountable human in a "
        "Co-authored-by trailer. Attributing agent work to a person is backlog "
        "0013, and on a repository with colleagues it attributes work to "
        "somebody who did not do it."
    )


def test_every_commit_under_the_rule_names_an_accountable_human():
    """Attribution is half of it; somebody must still be answerable."""
    offenders = [
        f"{c.sha[:8]} {c.name}"
        for c in commits_making_an_authorship_claim()
        if "Co-authored-by:" not in c.body
    ]
    assert offenders == [], (
        "these commits name nobody accountable:\n  " + "\n  ".join(offenders)
    )


# -- seat identity ------------------------------------------------
@pytest.mark.parametrize("name, email, expected", [
    pytest.param("kernel (agent)",
                 "darin+agent-kernel@users.noreply.github.com", True,
                 id="the shape the design specifies"),
    pytest.param("Darin Hoover", "darinh@gmail.com", False,
                 id="a person, which is the case that must fail"),
    pytest.param("Copilot", "223556219+Copilot@users.noreply.github.com", False,
                 id="a trailer identity is not an author identity"),
    pytest.param("management", "manager@example.com", False,
                 id="a word containing no agent marker"),
])
def test_the_identity_shape_check_separates_agents_from_people(name, email, expected):
    assert seat.is_agent_identity(name, email) is expected


def test_a_seat_identity_round_trips_as_an_agent():
    name, email = seat.seat_identity("api-refactor")
    assert seat.is_agent_identity(name, email)
    assert "api-refactor" in email


@pytest.mark.parametrize("bad", [
    pytest.param("a1b2c3d4", id="a bare hex prefix -- the session-id suggestion"),
    pytest.param("2f8c1e9a4b7d", id="a longer hash"),
    pytest.param("235a42ce-4546-41da", id="a uuid prefix"),
    pytest.param("session-4", id="named for a session"),
    pytest.param("kernel-2026", id="a trailing run number"),
])
def test_a_session_shaped_id_is_refused(bad):
    """A seat outlives its sessions, so it cannot be named after one.

    Deriving the identity from a session mints a new author on every restart:
    hundreds of one-off names in the log, and no per-seat history for effort
    estimates or calibration to be computed against. The refusal is loud
    because the damage is invisible until somebody asks a question the history
    can no longer answer.
    """
    with pytest.raises(seat.SeatIdError):
        seat.validate_seat_id(bad)


@pytest.mark.parametrize("good", ["kernel", "api-refactor", "billing-tests", "web-ui"])
def test_a_seat_named_for_its_work_is_accepted(good):
    assert seat.validate_seat_id(good) == good


def test_the_trailers_name_the_accountable_human_and_keep_the_session_out_of_the_name():
    trailers = seat.commit_trailers("kernel", session=42)
    assert any(t.startswith("Co-authored-by:") for t in trailers)
    assert any("kernel#42" in t for t in trailers)
    name, _ = seat.seat_identity("kernel")
    assert "42" not in name, "the session number leaked into the identity"
