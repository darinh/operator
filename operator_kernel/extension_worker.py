"""The process an extension's code actually runs in. Never the supervisor's.

Spawned by `extensions.Host` as `python extension_worker.py <target> <hook>
<token> <reply-path>`, with the hook's arguments as one JSON object on stdin,
and one JSON object written to the reply path. It answers exactly once and
exits, so a hook that hangs is cancelled by killing this process — which is the
only cancellation Python has on Windows, where a thread blocked in native code
cannot be interrupted.

**Nothing here is imported by kernel code**, and that is not incidental:
`tests/test_kernel_boundary.py` forbids the kernel importing anything outside
the standard library and itself, and this file is where third-party code gets
imported instead. It runs in a process the kernel started and can kill, rather
than in the process the kernel supervises nine seats from.

**The reply does not travel on stdout, and that is a correction.** It did, with
a token to pick it out of whatever else reached the stream, and two reviewers
found the same thing from opposite directions: an extension can write to
descriptor 1 from native code or from an `atexit` handler, so it can put noise
*after* the reply, and the host reads a bounded tail — so a megabyte of logging
pushes a `gate_change` that answered `block` out of the window, the host reports
a protocol violation, and fail-open turns the block into an allow. A stream an
extension may legitimately write to is not a protocol channel. stdout is now
entirely the extension's, kept only for diagnostics.

Python-level stdout is still pointed at stderr before the target module is
imported, so that a `print()` in a hook lands where a human debugging it will
look rather than in the noise the host truncates.

**Every ending is a reply.** An import that explodes, a hook that raises, a
`SystemExit` from a library that thinks it is a command-line tool, a return
value that will not serialise — each produces `{"ok": false}` naming what
happened, because a worker that dies silently leaves the host unable to tell a
broken extension from a broken pipe. The one ending that cannot reply is being
killed, and that is the host's deadline, which the host already knows about.

**"Has no opinion" and "does not implement this hook" are different**, and both
are different from "failed". A module without the attribute replies
`implemented: false`; a hook returning `None` replies with a null value. Neither
is an error, and neither becomes a claim.
"""
from __future__ import annotations

import importlib
import json
import sys
import traceback

#: Frames of traceback returned to the host. Enough to name the failing line in
#: the extension, short enough that a stack trace never becomes the payload.
TRACEBACK_LIMIT = 5


def _reply(stream, token: str, **fields) -> None:
    """Write one reply object and flush.

    The token goes on every reply including the failures. The reply channel is
    private to this call, so the token is not what keeps noise out any more --
    it is what distinguishes this worker's answer from a file left at that path
    by anything else, which is cheap enough to keep.
    """
    stream.write(json.dumps(dict(fields, token=token)) + "\n")
    stream.flush()


def main(argv: "list[str]", stdin=None, reply=None) -> int:
    """Import `target`, call `hook` with the arguments on stdin, reply.

    Arguments are passed rather than read from `sys` so this is testable in
    process, which matters more than it looks: a worker whose only test is an
    end-to-end spawn has its failure paths exercised by nothing, and its
    failure paths are the entire reason it exists.
    """
    stdin = sys.stdin if stdin is None else stdin
    saved_stdout = sys.stdout
    if len(argv) != 4:
        print(f"expected <target> <hook> <token> <reply-path>, got {argv!r}",
              file=sys.stderr)
        return 2
    target, hook, token, reply_path = argv

    handle, opened = reply, False
    if handle is None:
        try:
            handle = open(reply_path, "w", encoding="utf-8")
            opened = True
        except OSError as exc:
            # Nowhere to answer. The host sees no reply and reports a protocol
            # violation, which is the right verdict: this worker never spoke.
            print(f"reply path unusable: {exc}", file=sys.stderr)
            return 2

    try:
        try:
            raw = stdin.read()
            kwargs = json.loads(raw) if raw.strip() else {}
            if not isinstance(kwargs, dict):
                raise TypeError(
                    f"arguments must be an object, got {type(kwargs)}")
        except Exception as exc:
            _reply(handle, token, ok=False, error=type(exc).__name__,
                   detail=str(exc))
            return 1

        # Before the import, not after: module-level code runs on import, and a
        # banner printed there belongs with the hook's own output rather than
        # in the middle of it.
        sys.stdout = sys.stderr
        try:
            module = importlib.import_module(target)
        except BaseException as exc:
            _reply(handle, token, ok=False, error=type(exc).__name__,
                   detail=traceback.format_exc(limit=TRACEBACK_LIMIT))
            return 1

        fn = getattr(module, hook, None)
        if not callable(fn):
            _reply(handle, token, ok=True, implemented=False, value=None)
            return 0

        try:
            value = fn(**kwargs)
        except BaseException as exc:
            # `BaseException`, so that a library calling `sys.exit()` because it
            # thinks it is a command-line tool is reported as the extension
            # fault it is, rather than as a worker that vanished.
            _reply(handle, token, ok=False, error=type(exc).__name__,
                   detail=traceback.format_exc(limit=TRACEBACK_LIMIT))
            return 1

        try:
            # Serialised here so that an unserialisable answer is attributed to
            # the extension that gave it. Left to the host, it would arrive as a
            # protocol violation, which reads like a bug in this file.
            payload = json.dumps({"ok": True, "implemented": True,
                                  "value": value, "token": token},
                                 allow_nan=False)
        except (TypeError, ValueError) as exc:
            _reply(handle, token, ok=False, error="NotSerializable",
                   detail=f"{hook} returned {type(value).__name__}: {exc}")
            return 1
        handle.write(payload + "\n")
        handle.flush()
        return 0
    finally:
        # The stream that was there, not the one replies go to: under test
        # those differ, and restoring the wrong one leaves `sys.stdout`
        # pointing at the test's capture buffer for the rest of the process.
        sys.stdout = saved_stdout
        if opened:
            handle.close()


if __name__ == "__main__":  # pragma: no cover - exercised by spawning it
    sys.exit(main(sys.argv[1:]))
