"""Operators, named, running and not.

`active_instances` is reached through its module rather than bound at import.
A module-level `from` is resolved before a caller can substitute the roster.
"""
import supervisor_control
from supervisor_records import _running_loop_pid


def _section(heading: str, rows: list[str]) -> None:
    print(heading)
    if not rows:
        print("  (none)")
        return
    for index, row in enumerate(rows, 1):
        print(f"  {index}. {row}")


def list_instances() -> int:
    """Running operators, then the ones a clean stop left behind."""
    import operators
    import sys

    records = operators.all_operators()
    if records is None:
        print("could not read operators", file=sys.stderr)
        return 1
    if not records:
        print("No operators yet. Start one with: operator start")
        return 0
    running_ids = {inst.id for inst in supervisor_control.active_instances()}
    running, offline = [], []
    for op in records:
        if op.id in running_ids:
            pid = _running_loop_pid(op.instance())
            row = f"{op.name}  ({op.cwd})"
            if pid:
                row += f"  pid {pid}"
            running.append(row)
        else:
            offline.append(f"{op.name}  ({op.cwd})")
    _section("Running:", running)
    _section("Offline:", offline)
    return 0
