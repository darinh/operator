"""`python -m operator_cli.supervise` — the process a supervisor runs in.

`supervisor._spawn_background_loop` starts a supervisor by launching this, and
this does one thing: turn the arguments it was given into a `run_loop_mode`
call.

It is not a command a human runs. `--_supervise` is required precisely so that
somebody who mistakes it for one gets told, rather than starting an unattended
loop they did not ask for.
"""
from __future__ import annotations

import sys

from operator_kernel.argtail import at_dashdash

from .home import _bootstrap

#: Flags addressed to the supervisor. Everything else belongs to Copilot and is
#: passed through untouched -- argparse would reject those instead, and they are
#: not ours to rename.
_IGNORED = ("--_supervise", "--loop")


def parse(args: "list[str]") -> "tuple[str, list[str], bool]":
    """Returns (operator id, copilot_args, is_fresh)."""
    options, literal = at_dashdash(args)
    op_id, rest, fresh = "", [], False
    i = 0
    while i < len(options):
        arg = options[i]
        if arg in _IGNORED:
            pass
        elif arg == "--fresh":
            fresh = True
        elif arg == "--id":
            if i + 1 >= len(options) or not options[i + 1].strip():
                raise SystemExit("--id requires a value")
            op_id = options[i + 1]
            i += 1
        elif arg.startswith("--id="):
            op_id = arg.split("=", 1)[1]
            if not op_id.strip():
                raise SystemExit("--id requires a value")
        else:
            rest.append(arg)
        i += 1
    rest.extend(literal)
    return op_id, rest, fresh


def main(argv: "list[str] | None" = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if "--_supervise" not in args:
        print("operator_cli.supervise is how a supervisor process is started, "
              "not a command.", file=sys.stderr)
        print("  It is spawned for you by `operator start`.", file=sys.stderr)
        return 2
    op_id, copilot_args, is_fresh = parse(args)
    if not op_id.strip():
        raise SystemExit("--id is required")
    _bootstrap()
    import operators
    from supervisor import run_loop_mode
    record = operators.find(op_id)
    if record is None or record.id != op_id:
        print(f"no operator with id {op_id!r}", file=sys.stderr)
        return 2
    return run_loop_mode(record.instance(), copilot_args, is_fresh)


if __name__ == "__main__":  # pragma: no cover - exercised through main()
    sys.exit(main())
