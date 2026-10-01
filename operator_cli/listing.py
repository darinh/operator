"""Running operators, named.

`active_instances` is reached through its module rather than bound at import.
A module-level `from` is resolved before a caller can substitute the roster,
and `operator list` then reports an empty machine.
"""
import supervisor_control
from supervisor_records import _running_loop_pid


def list_instances() -> int:
    """Each running operator's name and supervisor pid."""
    found = supervisor_control.active_instances()
    if not found:
        print("No running seats.")
        return 0
    for inst in found:
        pid = _running_loop_pid(inst)
        if pid:
            print(f"  {inst.display_name}  pid {pid}")
        else:
            print(f"  {inst.display_name}")
    return 0
