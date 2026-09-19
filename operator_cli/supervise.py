"""`python -m operator_cli.supervise` — the process a supervisor runs in.

`supervisor._spawn_background_loop` starts a supervisor by launching this, and
this does one thing: turn the arguments it was given into a `run_loop_mode`
call. Every decision is a layer down, which is what this package is for.

It is not a command a human runs. `--_supervise` is required precisely so that
somebody who mistakes it for one gets told, rather than starting an unattended
loop they did not ask for.

**This module exists because the kernel had no way to start a supervisor.**
When the supervision loop was ported into `operator_kernel/supervisor.py`, the
spawn was pointed at that file and the argument handling was left behind in the
9,120-line module it came from. Nothing there read `--_supervise`, so every
spawned supervisor ran a file with no entry point, exited 0, and was reported
by nothing. `restart_loop` asks the old supervisor to detach *before* spawning
the replacement, so the visible result was a live session whose supervisor had
silently vanished.
"""
from __future__ import annotations

import sys

from .fleet import _bootstrap

#: Flags addressed to the supervisor. Everything else belongs to Copilot and is
#: passed through untouched -- argparse would reject those instead, and they are
#: not ours to rename.
_IGNORED = ("--_supervise", "--loop", "--headless", "--detached")


def parse(args: "list[str]") -> "tuple[str, list[str], bool, bool]":
    """Returns (name, copilot_args, is_fresh, adopt)."""
    name, rest, fresh, adopt = "", [], False, False
    i = 0
    while i < len(args):
        arg = args[i]
        if arg in _IGNORED:
            pass
        elif arg == "--fresh":
            fresh = True
        elif arg == "--adopt":
            adopt = True
        elif arg == "--name":
            if i + 1 >= len(args) or not args[i + 1].strip():
                raise SystemExit("--name requires a value")
            name = args[i + 1]
            i += 1
        elif arg.startswith("--name="):
            name = arg.split("=", 1)[1]
            if not name.strip():
                raise SystemExit("--name requires a value")
        else:
            rest.append(arg)
        i += 1
    return name, rest, fresh, adopt


def main(argv: "list[str] | None" = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if "--_supervise" not in args:
        print("operator_cli.supervise is how a supervisor process is started, "
              "not a command.", file=sys.stderr)
        print("  Use `operator-fleet` or `operator-seat`; this is spawned for "
              "you.", file=sys.stderr)
        return 2
    name, copilot_args, is_fresh, adopt = parse(args)
    if not name:
        raise SystemExit("--name is required")
    _bootstrap()
    from instance import Instance
    from supervisor import run_loop_mode
    return run_loop_mode(Instance(name), copilot_args, is_fresh, adopt=adopt)


if __name__ == "__main__":  # pragma: no cover - exercised through main()
    sys.exit(main())
