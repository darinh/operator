"""The ledger tail: reading an append-only file that somebody else rotates.

Cut out of `fleet_host.py` the day that file crossed `MAX_MODULE_LINES`, and a
cut rather than a bigger constant because there was a real seam to cut on. This
class reads a file; `fleet_host.py` asks extensions questions. Nothing here
knows what a hook is, and nothing there knows what a byte offset is.

**The three properties, and each is one of `docs/extensions.md` §8's
assumptions made true rather than assumed:**

**At-least-once.** The position advances in memory as records are read, and is
*persisted* only after the batch it covers has been handed over -- so a crash
mid-delivery redelivers. The caller that could not hand it over at all rewinds,
because an at-least-once guarantee that holds across restarts and not across
polls is not the one anybody assumed.

**Whole records only.** `evidence._append` writes a line with one `write`, but
"one write" is not "atomic" -- so a read that ends mid-line stops at the last
newline and leaves the remainder for the next poll. Without that, one torn read
is a permanently corrupted offset.

**Rotation is a rename, and a rename is not a shrink.** The ledger rotates at
8 MB by renaming itself to `trace.jsonl.1`, so the file at the path afterwards
is a *different file* wearing the same name. Two things follow, and both were
got wrong first:

* Detecting it by `size < offset` misses the case where the replacement ledger
  is already longer than the old position -- the read then starts partway into
  an unrelated file and reports nothing wrong. So the file is identified by
  `(st_dev, st_ino)` and a changed identity is the rotation.
* Draining the rotated copy in one bounded read and then switching abandons
  everything past `limit`. A rotation is 8 MB and a record is a few hundred
  bytes, so the rotated file holds tens of thousands of records and one batch
  is five hundred; the tail need only be behind for the rest to vanish with
  `gaps` still reading zero. Three reviewers from three model families found
  exactly this, independently, which is why the position is a *file* and an
  offset rather than an offset alone.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

#: How many ledger records one read may return. A bound on the batch rather
#: than on the tail: what is left over is delivered by the next read, because
#: the position advances only past records that were handed back.
MAX_FACTS_PER_POLL = 500


def tail_state_path(home) -> Path:
    """Where the tail remembers how far it read, so a restart is not a choice
    between replaying the whole ledger and skipping what arrived while it was
    down."""
    return Path(home) / "fleet-tail.json"


def _not_a_number(constant: str):
    """Refuse JSON's three non-finite constants. See `LedgerTail._parse`."""
    raise ValueError(f"{constant} is not a value a record may carry")

class LedgerTail:
    """An incremental reader over `trace.jsonl`, written not to raise.

    The three properties it holds — at-least-once, whole records only, and
    rotation-is-a-rename — are argued in this module's docstring rather than
    repeated here, because each of them is the reason this file exists rather
    than a detail of the class.
    """

    def __init__(self, path, state=None) -> None:
        self.path = Path(path)
        #: `with_suffix` and not `+ ".1"` on the string, so this tracks
        #: `evidence._rotate_if_needed` exactly rather than approximately.
        self.rotated = self.path.with_suffix(self.path.suffix + ".1")
        self.state = None if state is None else Path(state)
        self.unreadable = 0
        self.gaps = 0
        self.offset, self.identity = self._resume()

    def read(self, limit: int = MAX_FACTS_PER_POLL) -> "list[dict]":
        """Every record appended since the last read, oldest first.

        `self.identity` is the file this tail is *positioned in*, which after a
        rotation is the file now called `trace.jsonl.1` — not necessarily the
        one at `self.path`. The distinction is the fix for a defect two
        reviewers found independently: draining the rotated file in a single
        bounded read and then switching regardless abandons everything past
        `limit`, which on a rotation with a backlog is thousands of records
        lost while the read reports success.
        """
        lines: list[str] = []
        current = self._identity(self.path)
        if self.offset and self.identity is not None \
                and current != self.identity:
            lines, drained = self._rotated_remainder(limit)
            if not drained:
                # The rotated file had more than this batch could carry. Stay
                # positioned in it -- `self.identity` still names it -- and
                # take the rest next poll.
                return self._records(lines)
            self.offset = 0
        elif self._size(self.path) < self.offset:
            # Same file, fewer bytes: somebody truncated the ledger in place.
            # Nothing renamed it, so there is no copy to finish and the records
            # between here and there are gone. Counted rather than passed over,
            # because the whole point of this class is that a gap in the
            # evidence is itself evidence.
            self.gaps += 1
            self.offset = 0
        self.identity = current
        remaining = limit - len(lines)
        if remaining > 0:
            fresh, self.offset = self._read_from(self.path, self.offset,
                                                 remaining)
            lines.extend(fresh)
        return self._records(lines)

    def _records(self, lines) -> "list[dict]":
        return [r for r in (self._parse(line) for line in lines)
                if r is not None]

    def position(self) -> tuple:
        """Where this tail is, in a form `rewind` accepts."""
        return (self.offset, self.identity)

    def rewind(self, position: tuple) -> None:
        """Put the tail back where it was before a read that reached nobody.

        Without this, `read` advancing the in-memory cursor means a batch the
        host could not hand to any worker is lost for the life of the process:
        `remember` is skipped, so a *restart* recovers it, and nothing short of
        one does. At-least-once that only holds across restarts is not the
        property §8 assumed.
        """
        self.offset, self.identity = position

    def note_gap(self) -> None:
        """Record that records were lost somewhere other than in this class.

        Delivery has one such case -- a batch the host could not encode -- and
        it belongs on the same counter as a rotation gap, because from a
        reader's point of view they are the same event: evidence that existed
        and was not observed.
        """
        self.gaps += 1

    def remember(self) -> bool:
        """Persist the offset. Returns whether it was written.

        Called by `deliver` after the batch is out, never by `read`, because
        the gap between those two calls is exactly where at-least-once lives.

        The file's identity is persisted with it. Without that, a rotation
        while this process was *down* is invisible on the next start: the
        offset would be applied to whatever file now holds the name.

        Written through a temporary file and `os.replace`, which is atomic on
        both platforms this runs on. A crash partway through a plain write
        leaves a truncated JSON object, and this tail's own answer to an
        unreadable state file is to start over and count a gap -- so a
        non-atomic write here manufactures the loss it then reports.
        """
        if self.state is None:
            return False
        scratch = self.state.with_suffix(self.state.suffix + ".new")
        try:
            self.state.parent.mkdir(parents=True, exist_ok=True)
            scratch.write_text(json.dumps(
                {"path": str(self.path), "offset": int(self.offset),
                 "identity": list(self.identity or ())}), encoding="utf-8")
            os.replace(scratch, self.state)
            return True
        except (OSError, TypeError, ValueError):
            return False

    def _resume(self) -> "tuple[int, tuple | None]":
        """The remembered position, or the beginning — and the beginning on any
        doubt.

        The recorded path is checked against the one being followed. A state
        file left by a tail of a *different* ledger would otherwise seek this
        one to an offset that means nothing in it, and the read would begin in
        the middle of a record. Re-reading is at-least-once; seeking into the
        wrong file is neither.
        """
        if self.state is None:
            return 0, self._identity(self.path)
        try:
            saved = json.loads(self.state.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            saved = None
        if not isinstance(saved, dict) or saved.get("path") != str(self.path):
            return self._start_over()
        try:
            offset = max(0, int(saved.get("offset", 0)))
        except (TypeError, ValueError):
            return self._start_over()
        identity = saved.get("identity")
        if not isinstance(identity, list) or len(identity) != 2:
            # No identity to compare, so a rotation that happened while this
            # process was down cannot be detected. Start over rather than seek
            # a file that may not be the one the offset was measured in.
            return self._start_over()
        return offset, tuple(identity)

    def _start_over(self) -> "tuple[int, tuple | None]":
        """The beginning of the live ledger, with the loss counted if there was
        one.

        A reviewer asked for the rotated file to be replayed from zero here
        instead, on the grounds that re-reading is at-least-once and skipping
        is not. That is right about the principle and wrong about the cost: it
        replays up to 8 MB of already-delivered records into every subscriber
        the next time a state file is torn, and a notifier that mails on every
        fact then mails twenty thousand times because the host was killed
        mid-write. So the loss is *counted and announced* rather than either
        replayed or hidden -- and the state file is written atomically below,
        which is what makes this case rare enough for that to be the right
        trade.
        """
        if self.rotated.exists():
            self.gaps += 1
        return 0, self._identity(self.path)

    def _rotated_remainder(self, limit: int) -> "tuple[list[str], bool]":
        """What was still unread in the file that rotated away, and whether
        that file is now finished with.

        The second value is the correction two reviewers found independently.
        Reading `limit` lines and reporting success made a rotation with more
        than one batch of backlog drop everything past the first batch --
        silently, and reported as a clean read, which is the exact failure this
        class exists to refuse.
        """
        if self._identity(self.rotated) != self.identity:
            # Two rotations between polls, or a rename to somewhere this does
            # not look. Either way records existed that this tail will never
            # see, and a counter is the difference between a gap and a silence.
            self.gaps += 1
            return [], True
        lines, moved = self._read_from(self.rotated, self.offset, limit)
        progressed = moved > self.offset
        self.offset = moved
        if self.offset >= self._size(self.rotated):
            return lines, True
        if not progressed:
            # Bytes remain that will never become a line: the rotated file is
            # not written to any more, so a torn tail there is permanent.
            # Draining forever is the alternative, and it is a tail that never
            # reaches the live ledger again.
            self.gaps += 1
            return lines, True
        return lines, False

    @staticmethod
    def _identity(path: Path) -> "tuple | None":
        """`(device, inode)`, or `None` when there is no file to identify.

        Populated on Windows as well as POSIX — `os.stat` fills `st_ino` from
        the file index there — which matters because this is the platform the
        rest of this project is most careful about.
        """
        try:
            info = path.stat()
        except OSError:
            return None
        return (info.st_dev, info.st_ino)

    @staticmethod
    def _size(path: Path) -> int:
        try:
            return path.stat().st_size
        except OSError:
            return 0

    @staticmethod
    def _read_from(path: Path, offset: int, limit: int
                   ) -> "tuple[list[str], int]":
        """Whole lines from `offset`, and the offset just past the last of them.

        Bytes rather than text, and `errors="replace"` rather than `strict`.
        The ledger is written `ensure_ascii=False`, so it carries real UTF-8,
        and a decode error while *reading evidence* would stop the tail on
        exactly the record most worth reading. It is the same correction the
        worker's reader already carries one package over.
        """
        try:
            with open(path, "rb") as fh:
                fh.seek(offset)
                data = fh.read()
        except OSError:
            return [], offset
        if not data:
            return [], offset
        complete = data.split(b"\n")[:-1]
        if len(complete) > limit:
            complete = complete[:limit]
        consumed = sum(len(chunk) + 1 for chunk in complete)
        return ([chunk.decode("utf-8", "replace") for chunk in complete],
                offset + consumed)

    def _parse(self, line: str) -> "dict | None":
        """One ledger line as a record, or `None` with the loss counted.

        `parse_constant` refuses `NaN` and `Infinity`, which `json.loads`
        otherwise accepts happily. They matter here because a record carrying
        one is *unencodable on the way out*: `extensions.Host.call` serialises
        the payload with `allow_nan=False`, so a single such line would make
        the whole batch it lands in undeliverable to every extension. Refused
        where every other unreadable line is refused, one record rather than
        five hundred.
        """
        try:
            record = json.loads(line, parse_constant=_not_a_number)
        except ValueError:
            self.unreadable += 1
            return None
        if not isinstance(record, dict):
            self.unreadable += 1
            return None
        # Invariant 5 travels with the record. The ledger marks a claim; a
        # record with no `kind` is one the supervisor observed, and an
        # extension reading these must be able to tell the two apart without
        # knowing which events happen to be which.
        record.setdefault("kind", "fact")
        return record


