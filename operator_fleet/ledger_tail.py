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
        one at `self.path`. The distinction is the fix for a defect three
        reviewers found independently: draining the rotated file in a single
        bounded read and then switching regardless abandons everything past
        `limit`, which on a rotation with a backlog is thousands of records
        lost while the read reports success.

        The live file is **opened before it is identified**, and identified
        from that handle. `stat` then `open` is two looks at a name, and a
        rotation landing between them reads the replacement at the previous
        file's offset while recording that this tail is positioned in a file it
        never read — the same substitution this class exists to catch, one
        layer below where it was being caught.
        """
        lines: list[str] = []
        handle, current = self._open_identified(self.path)
        try:
            if self.identity is not None and current != self.identity:
                # No `self.offset and` guard here, and its absence is a fix
                # rather than a simplification: a tail that had just drained a
                # rotated file sits at offset zero, so the guard made a
                # rotation *at that moment* invisible and threw away the whole
                # 8 MB with `gaps` reading zero.
                lines, drained = self._rotated_remainder(limit)
                if not drained:
                    # The rotated file had more than this batch could carry.
                    # Stay positioned in it -- `self.identity` still names it
                    # -- and take the rest next poll.
                    return self._records(lines)
                self.offset = 0
            elif self._handle_size(handle) < self.offset:
                # Same file, fewer bytes: somebody truncated the ledger in
                # place. Nothing renamed it, so there is no copy to finish and
                # the records between here and there are gone. Counted rather
                # than passed over, because the whole point of this class is
                # that a gap in the evidence is itself evidence.
                self.gaps += 1
                self.offset = 0
            self.identity = current
            remaining = limit - len(lines)
            if remaining > 0 and handle is not None:
                fresh, self.offset = self._read_handle(handle, self.offset,
                                                       remaining)
                lines.extend(fresh)
        finally:
            if handle is not None:
                handle.close()
        return self._records(lines)

    def _records(self, lines) -> "list[dict]":
        return [r for r in (self._parse(line) for line in lines)
                if r is not None]

    def position(self) -> tuple:
        """Where this tail is, and what it has noticed, for `rewind`.

        The counters travel with the cursor. Two reviewers found the same thing
        when they were left out: a batch that reaches nobody rewinds and is
        re-read, so the malformed line or the torn rotated tail inside it is
        counted again on every retry, and `_report_tail` announces a fresh loss
        every fifteen seconds forever. A counter that inflates on retry is a
        gap report nobody can believe.
        """
        return (self.offset, self.identity, self.gaps, self.unreadable)

    def rewind(self, position: tuple) -> None:
        """Put the tail back where it was before a read that reached nobody.

        Without this, `read` advancing the in-memory cursor means a batch the
        host could not hand to any worker is lost for the life of the process:
        `remember` is skipped, so a *restart* recovers it, and nothing short of
        one does. At-least-once that only holds across restarts is not the
        property §8 assumed.
        """
        self.offset, self.identity, self.gaps, self.unreadable = position

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

        The second value is the correction all three reviewers found. Reading
        `limit` lines and reporting success made a rotation with more than one
        batch of backlog drop everything past the first batch -- silently, and
        reported as a clean read, which is the exact failure this class exists
        to refuse.
        """
        handle, identity = self._open_identified(self.rotated)
        try:
            if identity != self.identity:
                # Two rotations between polls, or a rename to somewhere this
                # does not look. Either way records existed that this tail will
                # never see, and a counter is the difference between a gap and
                # a silence.
                self.gaps += 1
                return [], True
            lines, moved = self._read_handle(handle, self.offset, limit)
            progressed = moved > self.offset
            self.offset = moved
            if self.offset >= self._handle_size(handle):
                return lines, True
            if not progressed:
                # Bytes remain that will never become a line: the rotated file
                # is not written to any more, so a torn tail there is
                # permanent. Draining forever is the alternative, and it is a
                # tail that never reaches the live ledger again.
                self.gaps += 1
                return lines, True
            return lines, False
        finally:
            if handle is not None:
                handle.close()

    @staticmethod
    def _open_identified(path: Path) -> "tuple":
        """Open `path` and identify the handle that was actually opened.

        `(handle, identity)`, either of which is `None` when there is nothing
        there. Never `stat` then `open`: those are two separate looks at a
        *name*, and a rotation between them hands back the replacement file's
        bytes with the previous file's identity. Identity comes from `fstat` on
        the descriptor, so the file this tail believes it is positioned in is
        the file the bytes came out of.

        `st_ino` is populated on Windows as well as POSIX — it is filled from
        the file index there — which matters because that is the platform the
        rest of this project is most careful about.

        On Windows a held handle also makes the rename *fail* rather than
        succeed behind this reader's back: `Path.replace` on the ledger raises
        `PermissionError` while a poll has it open. That is not a hazard here
        and is worth naming so nobody "fixes" it — `evidence._rotate_if_needed`
        catches `OSError` and passes, so the rotation it wanted simply happens
        on the next append, a few hundred microseconds later. The handle is
        held for one read and closed in a `finally`.
        """
        try:
            handle = open(path, "rb")
        except OSError:
            return None, None
        try:
            info = os.fstat(handle.fileno())
        except OSError:                                 # pragma: no cover
            handle.close()
            return None, None
        return handle, (info.st_dev, info.st_ino)

    @staticmethod
    def _identity(path: Path) -> "tuple | None":
        """`(device, inode)` by name, for the one caller that reads no bytes.

        `_resume` needs the identity of a file it is not about to read, so
        there is nothing to `fstat`. Everything that reads uses
        `_open_identified` instead, for the reason recorded there.
        """
        try:
            info = path.stat()
        except OSError:
            return None
        return (info.st_dev, info.st_ino)

    @staticmethod
    def _handle_size(handle) -> int:
        if handle is None:
            return 0
        try:
            return os.fstat(handle.fileno()).st_size
        except OSError:                                 # pragma: no cover
            return 0

    @staticmethod
    def _read_handle(handle, offset: int, limit: int
                     ) -> "tuple[list[str], int]":
        """Whole lines from `offset` of an already-open, already-identified
        file, and the offset just past the last of them.

        A handle rather than a path, so that the bytes and the identity in
        `read` come from the same file even if the name is rotated underneath
        mid-poll.

        Bytes rather than text, and `errors="replace"` rather than `strict`.
        The ledger is written `ensure_ascii=False`, so it carries real UTF-8,
        and a decode error while *reading evidence* would stop the tail on
        exactly the record most worth reading. It is the same correction the
        worker's reader already carries one package over.
        """
        if handle is None:
            return [], offset
        try:
            handle.seek(offset)
            data = handle.read()
        except (OSError, ValueError):
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


