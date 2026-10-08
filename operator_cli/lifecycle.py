"""Start, attach, stop, rename, and delete. Routing stays in entry."""
from __future__ import annotations

import re
import sys
from pathlib import Path

from operator_kernel.argtail import at_dashdash

from . import project


def _cwd_match(recorded: str, current) -> "bool | None":
    import paths
    try:
        want = current.resolve()
    except OSError:
        return None
    return paths.catalog_paths_match(want, recorded)


def _same_cwd(recorded: str, current) -> bool:
    return _cwd_match(recorded, current) is True


def _here() -> list:
    import operators
    here = Path.cwd()
    return [record for record in operators.all_operators() or []
            if _same_cwd(record.cwd, here)]


def default_name() -> str:
    """The operator working here, or else this directory's name. Empty if several."""
    here = _here()
    if len(here) > 1:
        return ""
    return here[0].name if here else Path.cwd().name


def _named(rest: list[str]) -> str:
    for arg in rest:
        if not arg.startswith("-"):
            return arg
    return ""


def _typed(record) -> str:
    """How to type ``record`` in a command: its id when a shell would change
    the name even inside quotes, else the name, quoted unless one plain word."""
    if re.search('["$%!\\\\`\u201c\u201d\u201e]', record.name):
        return record.id
    return record.name if re.fullmatch(r"[\w.-]+", record.name) else f'"{record.name}"'


def _listed(record) -> str:
    """``record`` in a list of what to type, with its name beside an id."""
    typed = _typed(record)
    return f"{typed} ({record.name})" if typed == record.id else typed


def _caller(verb: str):
    """The agent or person running this command, or None after saying why not."""
    import os
    from custody import caller
    who = caller(os.getpid())
    if isinstance(who, str):
        print(f"operator {verb}: {who}", file=sys.stderr)
        return None
    return who


def _lineage(who) -> tuple:
    """(parent, started_by_pid) for an operator ``who`` is starting."""
    import operators
    from custody import Agent
    if isinstance(who, Agent):
        return who.record.id, who.copilot_pid
    return operators.HUMAN, who.shell_pid


def start(rest: list[str]) -> int:
    from .entry import _bootstrap
    _bootstrap()
    name, fresh, attach_now, copilot, words = None, False, False, [], []
    options, literal = at_dashdash(rest)
    i = 0
    while i < len(options):
        arg = options[i]
        if arg in ("-h", "--help"):
            print("Usage: operator start [NAME] [--name NAME] [--agent AGENT] "
                  "[--attach] [--fresh] [task...]")
            return 0
        if arg == "--fresh":
            fresh = True
        elif arg == "--attach":
            attach_now = True
        elif arg == "--name" or arg.startswith("--name="):
            if arg == "--name":
                i += 1
                name = options[i] if i < len(options) else ""
            else:
                name = arg.split("=", 1)[1]
            if not name.strip():
                print("operator start --name needs a value", file=sys.stderr)
                return 2
        elif arg == "--agent":
            i += 1
            if i >= len(options) or not options[i].strip():
                print("operator start --agent needs a value", file=sys.stderr)
                return 2
            copilot += ["--agent", options[i]]
        elif arg.startswith("--agent="):
            copilot += ["--agent", arg.split("=", 1)[1]]
        elif arg.startswith("-"):
            copilot.append(arg)
            if "=" not in arg and i + 1 < len(options) and not options[i + 1].startswith("-"):
                i += 1
                copilot.append(options[i])
        else:
            words.append(arg)
        i += 1
    if name is None and words:
        name, words = words[0], words[1:]
    if words or literal[1:]:
        copilot += ["--", *words, *literal[1:]]
    import operators
    from supervisor import _spawn_background_loop
    from supervisor_control import active_instances, launch_status
    explicit = name is not None
    if not explicit:
        here = _here()
        if len(here) > 1:
            print(f"{len(here)} operators work here: {', '.join(map(_listed, here))}",
                  file=sys.stderr)
            print("pass a name: operator start NAME", file=sys.stderr)
            return 2
        name = default_name()
    record = operators.find(name) if name.strip() else None
    if record is not None and (explicit or _same_cwd(record.cwd, Path.cwd())):
        if any(item.id == record.id for item in active_instances()):
            if attach_now and not fresh and not copilot:
                print(f"{record.name} is already running")
                return _attach_when_up(record)
            print(f"{record.name} is already running", file=sys.stderr)
            return 1
    elif record is not None:
        print(f"an operator named {name!r} already works in {record.cwd}",
              file=sys.stderr)
        print("pass a name: operator start --name NAME", file=sys.stderr)
        return 2
    else:
        problem = operators.name_problem(name)
        if problem:
            print(problem, file=sys.stderr)
            return 2
        who = _caller("start")
        if who is None:
            return 1
        parent, started_by = _lineage(who)
        rc, guid, created = project.ensure_registered()
        if rc:
            return rc
        if created:
            print(f"registered this directory as a project ({guid})")
        try:
            record = operators.create(name, Path.cwd(), parent=parent,
                                      started_by_pid=started_by)
        except operators.BadName as exc:
            print(str(exc), file=sys.stderr)
            return 2
    from presence import dir_present
    if dir_present(Path(record.cwd)) is False:
        print(f"The directory '{record.name}' was working in no longer exists:",
              file=sys.stderr)
        print(f"  {record.cwd}", file=sys.stderr)
        return 1
    inst = record.instance()
    pid = _spawn_background_loop(inst, copilot, is_fresh=fresh, cwd=record.cwd)
    status, shown = launch_status(inst, pid)
    if status != "ready":
        print({"dead": f"operator {record.name} (pid {pid}) exited before the supervisor published"}.get(
            status, f"could not confirm supervisor for {record.name} (pid {pid})"),
              file=sys.stderr)
        return 1
    print(f"started {record.name} (pid {shown})")
    if attach_now:
        return _attach_when_up(record)
    return 0


def _attach_when_up(record) -> int:
    """Attach once the session exists. A supervisor between sessions has none."""
    from supervisor_control import wait_for_session
    if not wait_for_session(record.instance()):
        print(f"{record.name} has no session to attach to yet. "
              f"Try again: operator attach {_typed(record)}", file=sys.stderr)
        return 1
    return attach([record.name])


def attach(rest: list[str]) -> int:
    from .entry import _bootstrap
    _bootstrap()
    name = _named(rest)
    if not name:
        print("Usage: operator attach NAME", file=sys.stderr)
        return 2
    import operators
    from config import MUX
    from mux import MuxNotFoundError
    record = operators.find(name)
    if record is None:
        print(f"No operator '{name}'.", file=sys.stderr)
        return 1
    inst = record.instance()
    try:
        if not MUX.has_session(inst.session):
            print(f"No running operator '{record.name}'.", file=sys.stderr)
            return 1
        return MUX.attach(inst.session)
    except MuxNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return 1


def stop(rest: list[str]) -> int:
    from .entry import _bootstrap
    _bootstrap()
    name = _named(rest)
    if not name:
        print("Usage: operator stop NAME", file=sys.stderr)
        return 2
    import operators
    from supervisor_control import _request_supervisor_stop
    record = operators.find(name)
    if record is None:
        print(f"No operator '{name}'.", file=sys.stderr)
        return 1
    _request_supervisor_stop(record.instance())
    print(f"stop requested for {record.name}")
    return 0


def rename(rest: list[str]) -> int:
    from .entry import _bootstrap
    _bootstrap()
    names = [arg for arg in rest if not arg.startswith("-")]
    if len(names) != 2:
        print("Usage: operator rename NAME NEW", file=sys.stderr)
        return 2
    import operators
    record = operators.find(names[0])
    if record is None:
        print(f"No operator '{names[0]}'.", file=sys.stderr)
        return 1
    old = record.name
    try:
        updated = operators.rename(record, names[1])
    except operators.BadName as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(f"renamed {old} to {updated.name}")
    return 0


def delete(rest: list[str]) -> int:
    from .entry import _bootstrap, _ask
    _bootstrap()
    yes = False
    name = ""
    for arg in rest:
        if arg == "--yes":
            yes = True
        elif arg in ("-h", "--help"):
            print("Usage: operator delete NAME [--yes]")
            return 0
        elif arg.startswith("-"):
            print(f"unknown option: {arg}", file=sys.stderr)
            return 2
        elif not name:
            name = arg
        else:
            print("Usage: operator delete NAME [--yes]", file=sys.stderr)
            return 2
    if not name:
        print("Usage: operator delete NAME [--yes]", file=sys.stderr)
        return 2
    import operators
    from supervisor_control import active_instances
    from supervisor_records import _supervisor_present
    record = operators.find(name)
    if record is None:
        print(f"No operator '{name}'.", file=sys.stderr)
        return 1
    inst = record.instance()
    if (any(item.id == record.id for item in active_instances())
            or _supervisor_present(inst) is not None):
        print(f"stop it first: operator stop {_typed(record)}", file=sys.stderr)
        return 1
    if not yes:
        from . import argv as _argv
        if not _argv.isatty(sys.stdin):
            print("pass --yes to delete without a terminal", file=sys.stderr)
            return 2
        print(f"This deletes operator {record.name} and all of its settings.")
        print(f"Repo: {record.cwd}")
        answer = _ask("Delete? [y/N] ")
        if answer is None or answer.lower() not in ("y", "yes"):
            return 1
    if _delete_operator(record):
        return 1
    print(f"deleted {record.name}")
    return 0


def _must_keep_project(cwd: str) -> bool:
    import operators
    import paths
    from presence import dir_present
    others = operators.all_operators()
    failed = operators.unreadable()
    if others is None or failed is None or failed:
        return True
    root = paths.primary_repo_root(Path(cwd))
    for other in others:
        # A checkout that is gone no longer says which project it was part of.
        if dir_present(Path(other.cwd)) is not True:
            return True
        same = _cwd_match(str(paths.primary_repo_root(Path(other.cwd))), root)
        if same is None or same:
            return True
    return False


def _delete_operator(record) -> int:
    import operators
    import paths
    from config import CATALOG_UNREADABLE
    from probes import remove_file
    failed = list(record.instance().delete_files())
    located = paths.project_handoff_file(Path(record.cwd), record.id)
    if located is CATALOG_UNREADABLE:
        failed.append("the handoff (project catalog unreadable)")
    elif isinstance(located, Path) and not remove_file(located):
        failed.append(located)
    if failed:
        for path in failed:
            print(f"could not remove {path}", file=sys.stderr)
        return 1
    cwd = record.cwd
    operators.remove(record)
    if not _must_keep_project(cwd):
        project.forget(cwd)
    return 0
