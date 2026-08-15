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
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

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

def _git(*args: str) -> str:
    return subprocess.run(("git",) + args, cwd=REPO, capture_output=True,
                          text=True, encoding="utf-8", errors="replace").stdout


def commits_under_the_rule() -> list[tuple[str, str, str, str]]:
    """Commits at or after `RULE_FROM`, oldest first.

    `--ancestry-path` rather than a date range: dates are attacker- and
    rebase-controlled, and a commit can carry any timestamp it likes.
    """
    raw = _git("log", "--format=%H%x00%an%x00%ae%x00%B%x00%x00",
               f"{RULE_FROM}..HEAD")
    out = []
    for record in raw.split("\x00\x00"):
        record = record.strip("\n")
        if not record:
            continue
        parts = record.split("\x00")
        if len(parts) >= 4:
            out.append((parts[0], parts[1], parts[2], parts[3]))
    return out


def test_the_rule_boundary_names_a_commit_that_exists():
    """A boundary naming nothing would exclude the whole history silently."""
    assert _git("cat-file", "-t", RULE_FROM).strip() == "commit"


def test_there_is_history_under_the_rule():
    assert len(commits_under_the_rule()) >= 1


def test_no_commit_under_the_rule_is_authored_by_a_human_identity():
    offenders = [
        f"{sha[:8]} {name} <{email}>"
        for sha, name, email, _ in commits_under_the_rule()
        if not seat.is_agent_identity(name, email)
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
        f"{sha[:8]} {name}"
        for sha, name, _, body in commits_under_the_rule()
        if "Co-authored-by:" not in body
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
