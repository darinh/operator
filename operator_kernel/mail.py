"""Mail between an operator and its parent or its children.

Each recipient has a box under the operator home, named by operator id, or
HUMAN for the person. A message is one JSON file that moves from pending to
delivering to delivered by rename, so two readers never take the same one.
The recipient's own supervisor types its mail into the session, so two
senders never interleave keystrokes. A person reads with ``operator inbox``.

Delivery is at least once. A supervisor that dies after typing a message but
before filing it leaves the message in delivering, and the next supervisor
types it again.
"""
from __future__ import annotations

import json
import os
import secrets
import shutil
from pathlib import Path
from time import time, time_ns

from operators import HUMAN
from probes import log

#: The most characters one message may hold.
LIMIT = 4000
PENDING, DELIVERING, DELIVERED = "pending", "delivering", "delivered"
_FIELDS = ("from", "from_name", "relation", "text", "sent")
# A name may hold "]", and one that closed the header early could pose as the person.
_UNBRACKETED = str.maketrans("[]", "()")


def box(recipient: str) -> Path:
    import config
    return config.OPERATOR_HOME / "mail" / recipient


def post(recipient: str, message: dict) -> None:
    """File ``message`` for ``recipient``, whole or not at all, after every
    message already waiting, even within one tick of a coarse clock."""
    folder = box(recipient) / PENDING
    folder.mkdir(parents=True, exist_ok=True)
    newest = max((int(path.name[:20]) for path in _messages(folder)
                  if path.name[:20].isdigit()), default=0)
    name = f"{max(time_ns(), newest + 1):020d}-{secrets.token_hex(4)}.json"
    tmp = folder.parent / f"{name}.tmp"
    tmp.write_text(json.dumps(message), encoding="utf-8")
    os.replace(tmp, folder / name)


def take(recipient: str) -> "tuple[Path, dict] | None":
    """Claim the oldest waiting message, or None when none is waiting.

    Of two readers racing for one message, the rename succeeds for exactly
    one, and the other moves on to the next message.
    """
    held = box(recipient) / DELIVERING
    for path in _messages(box(recipient) / PENDING):
        try:
            held.mkdir(exist_ok=True)
            # A rename keeps the file's time, and requeue_stale reads a claim's
            # age from it. Stamped first, a claim is fresh the moment it exists.
            os.utime(path)
            os.rename(path, held / path.name)
        except OSError:
            continue
        message = _read(held / path.name)
        if message is not None:
            return held / path.name, message
        # Writes are whole, so only a hand edit gets here. Retrying it forever
        # would hold up every message behind it.
        filed(held / path.name)
    return None


def filed(path: Path) -> None:
    _move(path, DELIVERED)


def requeue(path: Path) -> None:
    _move(path, PENDING)


def requeue_stale(recipient: str, older_than: float = 0.0) -> None:
    """Put back what a reader claimed and never filed, because it died. A claim
    younger than ``older_than`` seconds may still have a live reader."""
    try:
        for path in _messages(box(recipient) / DELIVERING):
            if _claimed_for(path) >= older_than:
                requeue(path)
    except OSError as exc:
        log(f"  Could not requeue mail for {recipient}: {exc}")


def _claimed_for(path: Path) -> float:
    try:
        return time() - path.stat().st_mtime
    except OSError:
        return 0.0


def waiting(recipient: str) -> int:
    return len(_messages(box(recipient) / PENDING))


def forget(recipient: str) -> bool:
    """Delete the box. False when some of it is still there."""
    shutil.rmtree(box(recipient), ignore_errors=True)
    return not box(recipient).exists()


def line(message: dict) -> str:
    """The message as one line. The header names the sender, and means the
    line never starts with a character a Copilot session reads as a command."""
    if message["from"] == HUMAN:
        sender = "the person who started you"
    else:
        sender = (f"{message['from_name'].translate(_UNBRACKETED)} ({message['from']}), "
                  f"{message['relation']}")
    return f"[operator message from {_flat(sender)}] {_flat(message['text'])}"


def deliver(instance) -> int:
    """Type ``instance``'s waiting mail into its session. How many arrived.

    A failed keystroke puts its message back and ends the round, because the
    rest would fail the same way.
    """
    from config import MUX
    from mux import MuxError
    arrived = 0
    try:
        while (claimed := take(instance.id)) is not None:
            path, message = claimed
            try:
                MUX.send_keys(instance.session, line(message))
            except MuxError as exc:
                requeue(path)
                log(f"  Mail not delivered, kept for later: {exc}")
                break
            filed(path)
            arrived += 1
    except OSError as exc:
        log(f"  Mail delivery stopped: {exc}")
    if arrived:
        log(f"  Delivered {arrived} message(s)")
    return arrived


def _flat(text: str) -> str:
    return "".join(ch if ch.isprintable() else " " for ch in text).strip()


def _messages(folder: Path) -> list:
    try:
        return sorted(path for path in folder.iterdir() if path.suffix == ".json")
    except OSError:
        return []


def _read(path: Path) -> "dict | None":
    try:
        message = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(message, dict) or not all(
            isinstance(message.get(key), str) for key in _FIELDS):
        return None
    return message


def _move(path: Path, state: str) -> None:
    """Move a claimed message to ``state``. Moving one already gone is a no-op."""
    folder = path.parent.parent / state
    folder.mkdir(exist_ok=True)
    try:
        os.replace(path, folder / path.name)
    except FileNotFoundError:
        pass
