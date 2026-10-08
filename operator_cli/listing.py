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


def load() -> tuple:
    """The operators that loaded, or None when their directory could not be
    read, and a line for the user about each one that did not load."""
    import operators
    records, failed = operators.all_operators(), operators.unreadable()
    if records is None or failed is None:
        return None, ["could not read operators"]
    return records, [f"could not read {path}" for path in failed]


def list_instances() -> int:
    """Running operators, then the ones a clean stop left behind."""
    import sys

    records, problems = load()
    for line in problems:
        print(line, file=sys.stderr)
    if records is None:
        return 1
    if not records and not problems:
        print("No operators yet. Start one with: operator start")
        return 0
    running, offline = sections(records)
    _section("Running:", [label for _, label in running])
    _section("Offline:", [label for _, label in offline])
    return 0


def sections(records):
    """(running, offline) as (record, label) pairs, each child after its parent.

    A child is indented one step per generation and names its parent, which
    may sit in the other section.
    """
    import lineage
    running_ids = {inst.id for inst in supervisor_control.active_instances()}
    names, up = {op.id: op.name for op in records}, lineage.parents(records)
    running, offline = [], []
    for op, level in lineage.tree(records):
        label = f"{'  ' * (level - 1)}{op.name}  ({op.cwd})"
        if op.id in running_ids:
            pid = _running_loop_pid(op.instance())
            label = f"{label}  pid {pid}" if pid else label
        if level > 1:
            label += f"  child of {names[up[op.id]]}"
        (running if op.id in running_ids else offline).append((op, label))
    return running, offline
