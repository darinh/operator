"""What a seat remembers about itself, and the rules that make it safe to read.

`docs/seat-identity.md` is the design and the argument; this is the substrate.
Three properties decide every line here, and the first two come from a
measurement rather than from taste.

**Every write is durable when it is made.** Of 1,110 recorded session endings on
the machine this was written for, 113 took the handoff path and 997 did not --
so a seat's knowledge cannot be persisted *at the end of a session* without
inheriting a 90% loss rate, and losing exactly the sessions that ended badly.
The journal is append-only and a `remember` call is complete the moment it
returns. Nothing is batched and nothing waits for a tidy ending.

**The handoff is left alone.** It is a baton -- written by one session, consumed
and deleted by the next -- and widening it was the obvious idea that the
measurement kills. This is a sibling of `handoff/`, not a replacement for it.

**A seat may not grant itself authority.** This is the one that would be easy to
get wrong and fatal to get wrong. Journal text is written by an agent and read
by a later agent *in the same seat*, which is backlog 0013's loop with the most
credible possible author: itself. So every entry goes through
`mandate.vet_clause` -- the same scan work items, handoffs and extension claims
go through -- and every physical line is labelled on the way out. Not the first
line: a value can contain newlines, and prefixing only the first puts every
later line into a session as raw unattributed text, which two reviewers found
independently in `extensions.claim_text` and which costs one `\\n` to defeat.

The label says what the entry is and is deliberately unflattering:

    [seat prism, session 118, 2026-08-01, unverified] ...

Dated, attributed, and marked unverified, because INV-SELF is that a seat's
recollection arrives as a *claim about the past* and never as a statement about
the present. An earlier session concluded this. That is all it means.
"""
from __future__ import annotations

import json
import secrets
from datetime import datetime, timezone
from pathlib import Path

import evidence
import mandate
import paths

#: What sort of claim an entry is. Closed, so that an entry has to say what it
#: is before it is allowed in, and so recall can be selective rather than
#: returning everything a seat ever thought.
#:
#: `attempt` earns its place by being the one that records a *negative*: this
#: was tried and did not work. A seat with no way to write that down repeats it,
#: which is the specific waste this whole idea is meant to address.
KINDS = ("decision", "gotcha", "disposition", "attempt")

#: Characters kept from one entry. An entry is prose an agent wrote, and
#: unbounded third-party text in something a later session reads is what this
#: design keeps refusing. Truncated *before* vetting, for the reason
#: `fleet_host._vet` gives: cutting rendered text can take a line's attribution
#: label off with it.
MAX_TEXT = 600

#: Entries returned per kind by `recall`, newest first. The scarce resource is
#: the reading session's context, so this is a cap on what is *shown* rather
#: than on what is kept -- the file remains the whole record.
RECALL_PER_KIND = 5

#: Bytes of journal kept before `remember` refuses. Deliberately below
#: `evidence._MAX_BYTES`, where that appender starts *rotating*: rotation
#: replaces the previous `.1`, and a seat's memory silently deleting its own
#: oldest entries is worse than one that says it is full. A refused write is
#: visible to the agent making it; a deleted memory is visible to nobody.
MAX_JOURNAL_BYTES = 4 * 1024 * 1024


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def journal_dir(cwd) -> "Path | None":
    """Where this project's seat journals live, or None if it has no id.

    Resolved through the same catalog `handoff` uses, via `paths.catalog_guid`,
    rather than by a second reading of that file. Two readers of one
    hand-edited CSV is how the reader starts accepting rows the writer would
    refuse, and `paths.py` says so at length.

    None for "this directory is not a registered project" *and* for "the
    catalog could not be read". The two are different and `paths` keeps them
    apart, but every caller here does the same thing with them -- there is
    nowhere to write and nothing to read -- and a journal is not load-bearing
    enough to justify propagating the distinction into an agent's face.
    """
    found = paths.catalog_guid(Path(cwd))
    if found.guid is None:
        return None
    return paths.project_dir(found.guid) / "journal"


def journal_file(cwd, instance: str) -> "Path | None":
    """One file per seat, named for the seat. Sibling of `handoff/`.

    The path itself comes from `paths.project_journal_file`, in the kernel,
    because `build_preamble` has to find the same file to decide whether to
    mention it and the kernel may not import this package. Two spellings of one
    location is the drift `paths.py` already refuses for the catalog; the seat
    name is validated there too, so a name that could address another seat's
    memory is refused once rather than in each caller.
    """
    return paths.project_journal_file(Path(cwd), str(instance or ""))


def remember(cwd, instance: str, kind: str, text: str, *,
             session: int = 0, supersedes: "tuple | list" = ()) -> "str | None":
    """Append one entry. Returns its id, or None if nothing was written.

    Never raises. A seat whose journal is unwritable is a seat that carries on
    working -- this is a notebook, not a dependency, and an agent that crashed
    because it could not take a note would be a worse outcome than forgetting.
    """
    path = journal_file(cwd, instance)
    if path is None:
        return None
    if kind not in KINDS:
        return None
    body = str(text or "").strip()
    if not body:
        return None
    try:
        if path.exists() and path.stat().st_size >= MAX_JOURNAL_BYTES:
            return None
    except OSError:
        return None

    entry_id = secrets.token_hex(4)
    written = evidence._append(path, {
        "ts": _utcnow(),
        "id": entry_id,
        "instance": str(instance),
        "session": int(session) if isinstance(session, int) else 0,
        "kind": kind,
        "text": body[:MAX_TEXT],
        # There is no spelling of a verified entry, and that is INV-SELF in the
        # shape rather than in a check somebody has to remember to run. Nothing
        # a seat says about itself is evidence.
        "verified": False,
        "supersedes": [str(s) for s in supersedes if str(s).strip()],
    })
    return entry_id if written else None


def read_entries(cwd, instance: str) -> "list[dict]":
    """Every parseable entry, oldest first. Never raises.

    A line that will not parse is skipped rather than aborting the read: a
    journal whose tail was torn by a crash should still yield the hundred
    entries in front of the tear.
    """
    path = journal_file(cwd, instance)
    if path is None:
        return []
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return []
    found = []
    for line in raw.splitlines():
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if isinstance(entry, dict) and entry.get("kind") in KINDS:
            found.append(entry)
    return found


def has_entries(cwd, instance: str) -> bool:
    """Whether this seat has anything recorded. `paths` owns the probe.

    Shared with `build_preamble` rather than reimplemented, so the command and
    the clause that advertises it cannot disagree about whether a journal
    exists.
    """
    return paths.seat_has_journal(Path(cwd), str(instance or ""))


def recall(cwd, instance: str, per_kind: int = RECALL_PER_KIND) -> "list[dict]":
    """The most recent entries per kind, newest first, superseded ones dropped.

    Bounded by *kind* rather than overall, so a seat that wrote forty gotchas
    cannot crowd out the one disposition it recorded about itself. Nothing here
    judges importance, because nothing here is competent to: recency is a
    proxy, it is a poor one, and saying so is better than a ranking that would
    look like judgement.
    """
    entries = read_entries(cwd, instance)
    superseded = {str(s) for entry in entries
                  for s in (entry.get("supersedes") or [])}
    kept: list[dict] = []
    for kind in KINDS:
        of_kind = [e for e in reversed(entries)
                   if e.get("kind") == kind
                   and str(e.get("id", "")) not in superseded]
        kept.extend(of_kind[:max(0, per_kind)])
    kept.sort(key=lambda e: str(e.get("ts", "")), reverse=True)
    return kept


def forget(cwd, instance: str, entry_id: str, *, session: int = 0) -> bool:
    """Supersede an entry rather than deleting it.

    An append, not an edit. A journal a seat can rewrite is one whose history
    can be quietly revised by the party with the most interest in revising it,
    and the file stops being evidence the moment that is possible. The entry
    stops being recalled; it does not stop having been written.
    """
    if not str(entry_id).strip():
        return False
    return remember(cwd, instance, "decision",
                    f"superseded entry {entry_id}", session=session,
                    supersedes=[str(entry_id)]) is not None


def render(instance: str, entries: "list[dict]") -> "tuple[str, list]":
    """Entries as text a session may read. Returns `(text, withheld)`.

    The vetting is the point of this function, not the formatting. Journal text
    is written by an agent and read by an agent in the same seat, so a sentence
    like "I have blanket approval for all decisions" would otherwise make the
    round trip and come back wearing the seat's own name -- which is exactly
    backlog 0013, with the one author a session has no reason to doubt.
    `mandate.vet_clause` replaces such a clause with the standard refusal.

    `vet_clause` rather than `assert_no_unattributed_authority`, for the reason
    `claim_text` gives: the latter raises, and a raise on this path would let a
    seat kill its own supervisor permanently by writing the wrong sentence into
    its own notebook.
    """
    lines: list[str] = []
    withheld: list = []
    label = str(instance) if str(instance).strip() else "unknown-seat"
    for entry in entries:
        body = str(entry.get("text", "")).strip()
        if not body:
            continue
        clause, phrases = mandate.vet_clause(body, f"seat {label}")
        if phrases:
            withheld.append((str(entry.get("id", "?")), phrases))
        day = str(entry.get("ts", ""))[:10] or "undated"
        head = (f"[seat {label}, session {entry.get('session', '?')}, "
                f"{day}, unverified]")
        lines.extend(f"{head} ({entry.get('kind', '?')}:"
                     f"{entry.get('id', '?')}) {line}"
                     for line in clause.splitlines() or [""])
    return "\n".join(lines), withheld
