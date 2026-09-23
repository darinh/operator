"""`operator-recover` — bring back the seats a crash or a reboot took down.

A seat is not a process. It is an identity with a journal, a handoff and a
session number that accumulate over a project's life, and that is the whole
difference between this and a task runner: the longer a seat works somewhere,
the more it knows about it. Losing the machine should cost it the process, not
the continuity.

Nothing here decides anything. `supervisor_control` decides which seats were
running when the machine went down and what continuing one means; this parses
arguments and calls in.
"""
from __future__ import annotations

import argparse
import sys

from .fleet import _bootstrap, _settle_home


def _recover(args) -> int:
    # The home is settled *before* the kernel is imported, and that ordering is
    # the whole of it: `config.py` resolves `OPERATOR_HOME` at import and
    # derives `RESTART_DIR` from it there, so a `--home` applied afterwards
    # reaches nothing. Caught by running this against a planted fixture and
    # watching it list the developer's eleven real seats instead.
    _settle_home(args.home)
    _bootstrap()
    from supervisor_control import recover_loop, recoverable_instances
    from instance import Instance

    if args.name:
        return recover_loop(Instance(args.name))

    found = recoverable_instances()
    if not found:
        print("No seats need recovering.")
        return 0
    if not args.all:
        print(f"{len(found)} seat(s) were running when this machine last "
              f"stopped:")
        for inst in found:
            print(f"  {inst.display_name}")
        print("\n  Bring them all back with: operator recover --all")
        print("  Or one at a time with:    operator recover <name>")
        return 0

    # One that cannot be recovered must not decide the fate of the others: a
    # deleted working directory is a property of that seat, not of the machine.
    failed = 0
    for inst in found:
        failed += 1 if recover_loop(inst) else 0
    print(f"Recovered {len(found) - failed} of {len(found)} seat(s).")
    return 1 if failed else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="operator-recover",
        description="Restart the seats that were supervised when this machine "
                    "stopped, continuing each where it left off.")
    parser.add_argument("name", nargs="?", help="one seat (default: list them)")
    parser.add_argument("--all", action="store_true",
                        help="recover every seat that needs it")
    parser.add_argument("--home", help="operator state directory "
                                       "(default: ~/.operator)")
    parser.set_defaults(func=_recover)
    return parser


def main(argv: "list[str] | None" = None) -> int:
    args = build_parser().parse_args(sys.argv[1:] if argv is None else argv)
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover - exercised through main()
    sys.exit(main())
