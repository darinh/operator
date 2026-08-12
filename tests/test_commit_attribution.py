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

REPO = Path(__file__).resolve().parent.parent

#: Identities allowed to author commits here. A human name is deliberately not
#: on this list even though a human works on this repository: while the fleet
#: is what commits, an authored-by-human commit is the thing that needs
#: explaining, and an exception is a line somebody has to add on purpose.
HUMAN_AUTHORS: frozenset[str] = frozenset()

#: What an agent identity has to look like. Not a whitelist of names -- seats
#: are created freely and a list would go stale -- but a shape a human name
#: does not have.
AGENT_EMAIL = re.compile(r"^[^@]*\+agent[^@]*@|^[^@]*agent[^@]*@users\.noreply\.")
AGENT_NAME = re.compile(r"\(agent\)|^agent[-/]", re.I)


def _git(*args: str) -> str:
    return subprocess.run(("git",) + args, cwd=REPO, capture_output=True,
                          text=True, encoding="utf-8", errors="replace").stdout


def commits() -> list[tuple[str, str, str, str]]:
    """``(sha, author name, author email, body)`` for every commit here."""
    raw = _git("log", "--format=%H%x00%an%x00%ae%x00%B%x00%x00")
    out = []
    for record in raw.split("\x00\x00"):
        record = record.strip("\n")
        if not record:
            continue
        parts = record.split("\x00")
        if len(parts) >= 4:
            out.append((parts[0], parts[1], parts[2], parts[3]))
    return out


def is_agent_identity(name: str, email: str) -> bool:
    return bool(AGENT_EMAIL.search(email) or AGENT_NAME.search(name))


def test_there_is_history_to_check():
    """A scan over an empty log reports a clean repository."""
    assert len(commits()) >= 1


@pytest.mark.xfail(
    reason="the seat identity is not configured yet; this is the gap, recorded "
           "as a failing test rather than a note somebody has to remember",
    strict=False,
)
def test_no_commit_is_authored_by_a_human_identity():
    offenders = [
        f"{sha[:8]} {name} <{email}>"
        for sha, name, email, _ in commits()
        if not is_agent_identity(name, email) and name not in HUMAN_AUTHORS
    ]
    assert offenders == [], (
        "these commits are authored under an identity that reads as a person:\n  "
        + "\n  ".join(offenders)
        + "\n\nA seat commits as itself and names its accountable human in a "
        "Co-authored-by trailer. Attributing agent work to a person is backlog "
        "0013, and on a repository with colleagues it attributes work to "
        "somebody who did not do it."
    )


def test_every_commit_names_an_accountable_human():
    """Attribution is only half of it; somebody must still be answerable."""
    offenders = [
        f"{sha[:8]} {name}"
        for sha, name, _, body in commits()
        if "Co-authored-by:" not in body
    ]
    assert offenders == [], (
        "these commits name nobody accountable:\n  " + "\n  ".join(offenders)
    )


# ── controls ────────────────────────────────────────────────────
@pytest.mark.parametrize("name, email, expected", [
    pytest.param("api-refactor (agent)",
                 "darin+agent-api-refactor@users.noreply.github.com", True,
                 id="the shape the design specifies"),
    pytest.param("agent-kernel", "darin+agent-kernel@example.com", True,
                 id="name-prefixed agent"),
    pytest.param("Darin Hoover", "darinh@gmail.com", False,
                 id="a person, which is the case that must fail"),
    pytest.param("Copilot", "223556219+Copilot@users.noreply.github.com", False,
                 id="a trailer identity is not an author identity"),
    pytest.param("management", "manager@example.com", False,
                 id="a word containing no agent marker"),
])
def test_the_identity_shape_check_separates_agents_from_people(name, email, expected):
    assert is_agent_identity(name, email) is expected


def test_the_scan_reads_real_history_not_an_empty_string():
    """A `git log` that failed returns "" and every check would pass."""
    assert any(len(sha) == 40 for sha, _, _, _ in commits())
