"""The process an extension's code actually runs in. Never the supervisor's.

Spawned by `extensions.Host` as `python extension_worker.py <target> <hook>
<token>`, with the hook's arguments as one JSON object on stdin, and one JSON
object on stdout as the reply. It answers exactly once and exits, so a hook that
hangs is cancelled by killing this process — which is the only cancellation
Python has on Windows, where a thread blocked in native code cannot be
interrupted.

**Nothing here is imported by kernel code**, and that is not incidental:
`tests/test_kernel_boundary.py` forbids the kernel importing anything outside
the standard library and itself, and this file is where third-party code gets
imported instead. It runs in a process the kernel started and can kill, rather
than in the process the kernel supervises nine seats from.

**stdout is the protocol, so the extension does not get one.** Python-level
stdout is pointed at stderr before the target module is imported, because a
`print()` at import time — or a library that greets you on first use — would
otherwise land in the middle of the protocol. That is a guard against accident
and it has a hole with a name: an extension writing to file descriptor 1 from
native code goes straight round it, and can do so after the reply. Which is why
the reply carries `token`, echoed from the command line, and the host will not
read a reply-shaped line that does not carry it.

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

    The token goes on every reply including the failures, so that a worker
    which failed before it could do anything useful is still distinguishable
    from noise on the stream.
    """
    stream.write(json.dumps(dict(fields, token=token)) + "\n")
    stream.flush()


def main(argv: "list[str]", stdin=None, stdout=None) -> int:
    """Import `target`, call `hook` with the arguments on stdin, reply.

    Arguments are passed rather than read from `sys` so this is testable in
    process, which matters more than it looks: a worker whose only test is an
    end-to-end spawn has its failure paths exercised by nothing, and its
    failure paths are the entire reason it exists.
    """
    stdin = sys.stdin if stdin is None else stdin
    real_stdout = sys.stdout if stdout is None else stdout
    saved_stdout = sys.stdout
    if len(argv) != 3:
        _reply(real_stdout, "", ok=False, error="BadInvocation",
               detail=f"expected <target> <hook> <token>, got {argv!r}")
        return 2
    target, hook, token = argv

    try:
        raw = stdin.read()
        kwargs = json.loads(raw) if raw.strip() else {}
        if not isinstance(kwargs, dict):
            raise TypeError(f"arguments must be an object, got {type(kwargs)}")
    except Exception as exc:
        _reply(real_stdout, token, ok=False, error=type(exc).__name__,
               detail=str(exc))
        return 1

    # Before the import, not after: module-level code runs on import, and a
    # banner printed there is exactly as damaging to the protocol as one
    # printed from the hook.
    sys.stdout = sys.stderr
    try:
        try:
            module = importlib.import_module(target)
        except BaseException as exc:
            _reply(real_stdout, token, ok=False, error=type(exc).__name__,
                   detail=traceback.format_exc(limit=TRACEBACK_LIMIT))
            return 1

        fn = getattr(module, hook, None)
        if not callable(fn):
            _reply(real_stdout, token, ok=True, implemented=False, value=None)
            return 0

        try:
            value = fn(**kwargs)
        except BaseException as exc:
            # `BaseException`, so that a library calling `sys.exit()` because it
            # thinks it is a command-line tool is reported as the extension
            # fault it is, rather than as a worker that vanished.
            _reply(real_stdout, token, ok=False, error=type(exc).__name__,
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
            _reply(real_stdout, token, ok=False, error="NotSerializable",
                   detail=f"{hook} returned {type(value).__name__}: {exc}")
            return 1
        real_stdout.write(payload + "\n")
        real_stdout.flush()
        return 0
    finally:
        # The stream that was there, not the one replies go to: under test
        # those differ, and restoring the wrong one leaves `sys.stdout`
        # pointing at the test's capture buffer for the rest of the process.
        sys.stdout = saved_stdout


if __name__ == "__main__":  # pragma: no cover - exercised by spawning it
    sys.exit(main(sys.argv[1:]))
