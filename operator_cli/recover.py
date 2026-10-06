"""`operator recover` brings back the operators a crash or a reboot took down.

Nothing here decides anything. `supervisor_control` decides which operators were
running when the machine went down and what continuing one means; this parses
arguments and calls in.
"""
from __future__ import annotations

import argparse
import sys

from .home import _bootstrap, _settle_home


def _recover(args) -> int:
    # The home is settled *before* the kernel is imported, and that ordering is
    # the whole of it: `config.py` resolves `OPERATOR_HOME` at import and
    # derives `RESTART_DIR` from it there, so a `--home` applied afterwards
    # reaches nothing.
    _settle_home(args.home)
    _bootstrap()
    from supervisor_control import recover_loop, recoverable_instances
    import operators

    names = list(args.name or [])
    if names and args.all:
        print("pass names or --all, not both", file=sys.stderr)
        return 2
    if names:
        failed = 0
        for name in names:
            record = operators.find(name)
            if record is None:
                print(f"No operator '{name}'.", file=sys.stderr)
                failed += 1
                continue
            failed += 1 if recover_loop(record.instance()) else 0
        return 1 if failed else 0

    found = recoverable_instances()
    if not found:
        print("No operators need recovering.")
        return 0
    if not args.all:
        print(f"{len(found)} operator(s) were running when this machine last "
              f"stopped:")
        for inst in found:
            print(f"  {inst.display_name}")
        print("\n  Bring them all back with: operator recover --all")
        print("  Or one at a time with:    operator recover <name>")
        return 0

    # One that cannot be recovered must not decide the fate of the others: a
    # deleted working directory is a property of that operator, not of the machine.
    failed = 0
    for inst in found:
        failed += 1 if recover_loop(inst) else 0
    print(f"Recovered {len(found) - failed} of {len(found)} operator(s).")
    return 1 if failed else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="operator recover", allow_abbrev=False,
        description="Restart the operators that were supervised when this machine "
                    "stopped, continuing each where it left off.")
    parser.add_argument("name", nargs="*", help="operators to recover (default: list them)")
    parser.add_argument("--all", action="store_true",
                        help="recover every operator that needs it")
    parser.add_argument("--home", help="operator state directory "
                                       "(default: ~/.operator)")
    parser.set_defaults(func=_recover)
    return parser


def main(argv: "list[str] | None" = None) -> int:
    args = build_parser().parse_args(sys.argv[1:] if argv is None else argv)
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover - exercised through main()
    sys.exit(main())
