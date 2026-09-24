"""`operator` -- the front door.

No arguments and a TTY on stdin and stdout opens a numbered menu. No TTY
prints help and exits non-zero, so CI cannot hang on a prompt. A verb on the
command line skips the menu. The menu prints the equivalent command before
it runs, so the flags are learnable by use.

`operator-fleet`, `operator-seat` and `operator-recover` stay installed.
This calls the same functions they do.
"""
from __future__ import annotations

import json
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

from operator_kernel.argtail import at_dashdash

from . import argv as _argv
from . import ext, fleet, project, recover, seat
from .fleet import _bootstrap, _home, _settle_home


@dataclass(frozen=True)
class Verb:
    tokens: tuple[str, ...]
    help: str
    menu: str
    prompts: tuple[str, ...] = ()
    extra_menu: tuple[tuple[str, tuple[str, ...]], ...] = ()


@dataclass(frozen=True)
class Item:
    label: str
    argv: tuple[str, ...]
    prompts: tuple[str, ...] = ()


VERBS: tuple[Verb, ...] = (
    Verb(("doctor",), "check that this machine can run operator", "Check this machine"),
    Verb(("start",), "start a supervised seat (start --name NAME)", "Start a supervised seat"),
    Verb(("list",), "list running seats", "List running seats"),
    Verb(("join",), "attach this terminal to a running seat", "Join a running seat", ("name",)),
    Verb(("stop",), "ask a seat's supervisor to stop", "Stop a supervised seat", ("name",)),
    Verb(("restart-loop",), "replace a supervisor without stopping the session",
         "Restart one seat's supervisor", ("name",), extra_menu=(("Restart every running supervisor", ("restart-loop", "--all")),)),
    Verb(("recover",), "list seats that need recovering after a crash", "List seats that need recovering", extra_menu=(("Recover every seat that needs it", ("recover", "--all")),)),
    Verb(("project", "register"), "register this directory as a project", "Register this directory as a project"),
    Verb(("project", "list"), "list registered projects", "List registered projects"),
    Verb(("project", "forget"), "remove a registration, keep the journal", "Forget a project registration", ("path",)),
    Verb(("ext", "list"), "list registered extensions", "List extensions"),
    Verb(("ext", "enable"), "enable an extension", "Enable an extension", ("extension",)),
    Verb(("ext", "disable"), "disable an extension", "Disable an extension", ("extension",)),
    Verb(("remember",), "record one claim for this seat", "Remember something about this seat", ("instance", "kind", "text")),
    Verb(("recall",), "show what earlier sessions recorded", "Recall what this seat recorded", ("instance",)),
    Verb(("forget",), "stop recalling one journal entry", "Forget one journal entry", ("instance", "id")),
    Verb(("fleet", "run"), "poll the ledger and ask the extensions", "Run the fleet host"),
    Verb(("fleet", "proposals"), "show what is waiting for a human", "Show fleet proposals"),
    Verb(("trace",), "show recent ledger records, newest first", "Show recent ledger records"),
    Verb(("verify",), "check the ledger chain", "Verify the ledger chain"),
)

_PROMPT_LABEL = {
    "name": ("Seat name: ", "seat name"),
    "instance": ("Seat name: ", "seat name"),
    "kind": ("Kind (decision, gotcha, disposition, attempt): ", "kind"),
    "text": ("Text: ", "note"),
    "id": ("Entry id: ", "entry id"),
    "path": ("Project directory: ", "directory"),
    "extension": ("Extension name: ", "extension"),
}


def menu_items() -> tuple[Item, ...]:
    items = []
    for verb in VERBS:
        items.append(Item(verb.menu, verb.tokens, verb.prompts))
        for label, argv in verb.extra_menu:
            items.append(Item(label, argv))
    return tuple(items)


def _print_help(stream) -> None:
    print("Usage: operator [command]", file=stream)
    print("No command opens a menu when stdin and stdout are a TTY.", file=stream)
    print(file=stream)
    for verb in VERBS:
        print(f"  {' '.join(verb.tokens):<22}{verb.help}", file=stream)


def _peel_home(argv: list[str]) -> tuple["str | None", list[str]]:
    options, literal = at_dashdash(argv)
    home, rest = None, []
    i = 0
    while i < len(options):
        arg = options[i]
        if arg == "--home":
            if i + 1 >= len(options):
                print("operator --home needs a directory", file=sys.stderr)
                raise SystemExit(2)
            home = options[i + 1]
            i += 2
            continue
        if arg.startswith("--home="):
            home = arg.split("=", 1)[1]
            i += 1
            continue
        rest.append(arg)
        i += 1
    rest.extend(literal)
    return home, rest


def _ask(prompt: str) -> "str | None":
    try:
        return input(prompt).strip()
    except EOFError:
        print()
        return None


def _ask_needed(prompt: str, what: str) -> "str | None":
    while True:
        value = _ask(prompt)
        if value is None:
            return None
        if value:
            return value
        print(f"A {what} is needed.")


def _build_argv(item: Item, values: dict[str, str]) -> list[str]:
    argv = list(item.argv)
    if "instance" in values:
        argv = [argv[0], "--instance", values["instance"], *argv[1:]]
    if "kind" in values:
        argv += ["--kind", values["kind"]]
    for key in ("name", "text", "id", "path", "extension"):
        if key in values:
            argv += [values[key]]
    return argv


def _prompt_start() -> "list[str] | None":
    name = _ask_needed("Seat name: ", "seat name")
    if name is None:
        return None
    work = _ask("What should it work on: ")
    if work is None:
        return None
    agent = _ask("Agent (Enter for Copilot CLI default): ")
    if agent is None:
        return None
    attach = _ask("Attach now? [y/N]: ")
    if attach is None:
        return None
    argv = ["start", "--name", name]
    if agent:
        argv += ["--agent", agent]
    if attach.lower() in ("y", "yes"):
        argv.append("--attach")
    if work:
        argv.append(work)
    return argv


def _menu() -> int:
    items = menu_items()
    print("What do you want to do?")
    print()
    for index, item in enumerate(items, 1):
        print(f"  {index}. {item.label}")
    print("  0. Quit")
    print()
    while True:
        choice = _ask("Choice: ")
        if choice is None:
            return 2
        if choice in ("0", "q", "Q"):
            return 0
        if choice == "":
            print("A choice is needed.")
            continue
        try:
            number = int(choice)
        except ValueError:
            print("Not a number.")
            continue
        if number < 1 or number > len(items):
            print("No such choice.")
            continue
        break
    item = items[number - 1]
    if item.argv == ("start",):
        argv = _prompt_start()
        if argv is None:
            return 2
        print("Running: operator " + _argv.quote_argv(argv))
        return dispatch(argv)
    values = {}
    for key in item.prompts:
        prompt, what = _PROMPT_LABEL[key]
        value = _ask_needed(prompt, what)
        if value is None:
            return 2
        values[key] = value
    argv = _build_argv(item, values)
    print("Running: operator " + _argv.quote_argv(argv))
    return dispatch(argv)


def _start(rest: list[str]) -> int:
    _bootstrap()
    name, fresh, attach, copilot = "", False, False, []
    options, literal = at_dashdash(rest)
    i = 0
    while i < len(options):
        arg = options[i]
        if arg in ("-h", "--help"):
            print("Usage: operator start --name NAME [--agent AGENT] "
                  "[--attach] [--fresh] [prompt...]")
            return 0
        if arg == "--fresh":
            fresh = True
        elif arg == "--attach":
            attach = True
        elif arg == "--name":
            i += 1
            if i >= len(options) or not options[i].strip():
                print("operator start --name needs a value", file=sys.stderr)
                return 2
            name = options[i]
        elif arg.startswith("--name="):
            name = arg.split("=", 1)[1]
        elif arg == "--agent":
            i += 1
            if i >= len(options) or not options[i].strip():
                print("operator start --agent needs a value", file=sys.stderr)
                return 2
            copilot += ["--agent", options[i]]
        elif arg.startswith("--agent="):
            copilot += ["--agent", arg.split("=", 1)[1]]
        else:
            copilot.append(arg)
        i += 1
    copilot.extend(literal)
    if not name.strip() and copilot and not copilot[0].startswith("-"):
        name, copilot = copilot[0], copilot[1:]
    if not name.strip():
        print("Usage: operator start --name NAME [--agent AGENT] [--attach] "
              "[--fresh] [prompt...]", file=sys.stderr)
        return 2
    from instance import Instance
    from supervisor import _spawn_background_loop
    from supervisor_control import launch_status, wait_for_session
    rc, guid, created = project.ensure_registered()
    if rc:
        return rc
    if created:
        print(f"registered this directory as a project ({guid})")
    inst = Instance(name)
    pid = _spawn_background_loop(inst, copilot, is_fresh=fresh)
    status, shown = launch_status(inst, pid)
    if status != "ready":
        print({"dead": f"seat {name} (pid {pid}) exited before the supervisor published"}.get(
            status, f"could not confirm supervisor for {name} (pid {pid})"), file=sys.stderr)
        return 1
    print(f"started {name} (pid {shown})")
    if attach:
        wait_for_session(inst)
        return _join([name])
    return 0


def _list(_rest: list[str]) -> int:
    _bootstrap()
    from supervisor_control import active_instances
    found = active_instances()
    if not found:
        print("No running seats.")
        return 0
    for inst in found:
        print(f"  {inst.display_name}")
    return 0


def _named(rest: list[str]) -> str:
    for arg in rest:
        if not arg.startswith("-"):
            return arg
    return ""


def _join(rest: list[str]) -> int:
    _bootstrap()
    name = _named(rest)
    if not name:
        print("Usage: operator join NAME", file=sys.stderr)
        return 2
    from config import MUX
    from instance import Instance
    from mux import MuxNotFoundError
    inst = Instance(name)
    try:
        if not MUX.has_session(inst.session):
            print(f"No running seat '{name}'.", file=sys.stderr)
            return 1
        return MUX.attach(inst.session)
    except MuxNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return 1


def _stop(rest: list[str]) -> int:
    _bootstrap()
    name = _named(rest)
    if not name:
        print("Usage: operator stop NAME", file=sys.stderr)
        return 2
    from instance import Instance
    from supervisor_control import _request_supervisor_stop
    inst = Instance(name)
    if not inst.is_managed():
        print(f"No seat '{name}'.", file=sys.stderr)
        return 1
    _request_supervisor_stop(inst)
    print(f"stop requested for {name}")
    return 0


def _doctor(_rest: list[str]) -> int:
    _bootstrap()
    failed = 0
    found = shutil.which("copilot")
    if found:
        print(f"copilot: {found}")
    else:
        print("copilot: not found on PATH")
        print("  Install GitHub Copilot CLI and make sure copilot runs.")
        failed = 1
    from config import MUX
    if MUX.available():
        print(f"multiplexer: {MUX.binary}")
    else:
        from mux import _install_hint
        print("multiplexer: not found")
        print(_install_hint())
        failed = 1
    home = _home(None)
    try:
        home.mkdir(parents=True, exist_ok=True)
        probe = home / ".doctor-write"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        print(f"home: writable at {home}")
    except OSError as exc:
        print(f"home: cannot write {home} ({exc})")
        print("  Pass --home to a directory you can write.")
        failed = 1
    if failed:
        print("doctor: failed")
        return 1
    print("doctor: ok")
    return 0


def _restart_loop(rest: list[str]) -> int:
    _bootstrap()
    from supervisor_control import restart_all_loops, restart_loop
    options, literal = at_dashdash(rest)
    if "--all" in options:
        return restart_all_loops()
    name = _named(options)
    if not name and len(literal) > 1:
        name = literal[1]
    return restart_loop(name or None)


def _recover(rest: list[str]) -> int:
    return recover.main(rest)


def _seat_cmd(verb: str, rest: list[str]) -> int:
    options, literal = at_dashdash(rest)
    parent: list[str] = []
    child: list[str] = []
    i = 0
    while i < len(options):
        arg = options[i]
        if arg in ("--instance", "--session"):
            if i + 1 >= len(options):
                print(f"operator {verb} {arg} needs a value", file=sys.stderr)
                return 2
            parent += [arg, options[i + 1]]
            i += 2
            continue
        if arg.startswith("--instance=") or arg.startswith("--session="):
            parent.append(arg)
            i += 1
            continue
        child.append(arg)
        i += 1
    child.extend(literal)
    return seat.main([*parent, verb, *child], prog="operator")


def _remember(rest: list[str]) -> int:
    return _seat_cmd("remember", rest)


def _recall(rest: list[str]) -> int:
    return _seat_cmd("recall", rest)


def _forget(rest: list[str]) -> int:
    return _seat_cmd("forget", rest)


def _fleet(rest: list[str]) -> int:
    if not rest or rest[0] not in ("run", "proposals", "-h", "--help"):
        print("Usage: operator fleet run|proposals", file=sys.stderr)
        return 2
    return fleet.main(rest)


def _ledger_paths() -> "list[Path]":
    """Every file holding ledger records, oldest first.

    Rotation is a rename, so `trace.jsonl.1` holds records no less real than
    the live file's. `trace` and `verify` each built this list once and drifted:
    `trace` guarded on the rotated file existing and then read only the live
    one, so a home that had rotated with nothing written since printed nothing
    and exited zero. One list, so a third reader cannot miss the same half.
    """
    path = _home(None) / "trace.jsonl"
    rotated = path.with_suffix(path.suffix + ".1")
    return [p for p in (rotated, path) if p.exists()]


def _trace(rest: list[str]) -> int:
    _bootstrap()
    n = 20
    i = 0
    while i < len(rest):
        arg = rest[i]
        if arg in ("-n", "--lines"):
            i += 1
            if i >= len(rest):
                print("operator trace -n needs a value", file=sys.stderr)
                return 2
            try:
                n = int(rest[i])
            except ValueError:
                print("operator trace -n needs an integer", file=sys.stderr)
                return 2
        elif arg.startswith("-n") and arg != "-n":
            try:
                n = int(arg[2:])
            except ValueError:
                print("operator trace -n needs an integer", file=sys.stderr)
                return 2
        elif arg in ("-h", "--help"):
            print("Usage: operator trace [-n N]")
            return 0
        else:
            print(f"operator trace: unexpected argument {arg}",
                  file=sys.stderr)
            return 2
        i += 1
    if n < 0:
        print("operator trace -n needs a non-negative integer",
              file=sys.stderr)
        return 2
    import ledger_tail
    paths = _ledger_paths()
    if not paths:
        print(f"no ledger at {_home(None) / 'trace.jsonl'}")
        return 0
    records: list[dict] = []
    lost = 0
    for each in paths:
        tail = ledger_tail.LedgerTail(each, state=None)
        records.extend(tail.snapshot())
        lost += tail.unreadable
    records.reverse()
    for record in records[:n]:
        print(json.dumps(record, ensure_ascii=True, default=str))
    if lost:
        print(f"{lost} line(s) in the ledger could not be read",
              file=sys.stderr)
    return 0


def _verify(_rest: list[str]) -> int:
    _bootstrap()
    import ledger_chain
    result = ledger_chain.verify(_ledger_paths())
    if isinstance(result, ledger_chain.Verified):
        print(f"verified: {result.records} record(s), "
              f"{result.writers} writer(s)")
        return 0
    if isinstance(result, ledger_chain.NoChain):
        print(f"no chain: {result.records} record(s)")
        return 0
    if isinstance(result, ledger_chain.TruncatedTail):
        print(f"truncated tail: {result.bytes_dropped} byte(s) dropped")
        return 1
    if isinstance(result, ledger_chain.Gap):
        print(f"gap: writer {result.writer} after {result.after_seq} "
              f"before {result.before_seq}")
        return 1
    if isinstance(result, ledger_chain.Broken):
        print(f"broken: writer {result.writer} seq {result.seq} "
              f"({result.reason})")
        return 1
    print(type(result).__name__)
    return 1


HANDLERS = {
    "doctor": _doctor,
    "start": _start,
    "list": _list,
    "join": _join,
    "stop": _stop,
    "restart-loop": _restart_loop,
    "recover": _recover,
    "project": project.main,
    "ext": ext.main,
    "remember": _remember,
    "recall": _recall,
    "forget": _forget,
    "fleet": _fleet,
    "trace": _trace,
    "verify": _verify,
}


def dispatch(argv: list[str], home: "str | None" = None) -> int:
    """Run one verb, with the home settled first.

    Every verb reaches its handler through here, from typed argv and from the
    menu alike, so settling here is what makes the export unskippable. It used
    to sit in `main` only: a seat started from the menu spawned its child
    without the export, and the two agreed on the home by coincidence rather
    than by construction.

    This is the only settle on the *routing* path, not in the package.
    `fleet.py` and `recover.py` settle again inside the delegated CLIs, which
    still have their own `--home` and are still reachable as their own console
    scripts. Settling twice is harmless -- it resolves and exports the same
    string -- and removing it would leave `operator-fleet` unsettled.
    """
    _settle_home(home)
    verb = argv[0]
    handler = HANDLERS.get(verb)
    if handler is None:
        print(f"unknown command: {verb}", file=sys.stderr)
        _print_help(sys.stderr)
        return 2
    try:
        return handler(argv[1:])
    except SystemExit as exc:
        code = exc.code
        if code is None:
            return 0
        return code if isinstance(code, int) else 1


def main(argv: "list[str] | None" = None) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)
    if not raw:
        if _argv.isatty(sys.stdin) and _argv.isatty(sys.stdout):
            return _menu()
        _print_help(sys.stderr)
        return 2
    if raw[0] in ("-h", "--help", "help"):
        _print_help(sys.stdout)
        return 0
    try:
        home, rest = _peel_home(raw)
    except SystemExit as exc:
        code = exc.code
        return 2 if code is None else (code if isinstance(code, int) else 2)
    if not rest:
        _print_help(sys.stderr)
        return 2
    if rest[0] in ("-h", "--help", "help"):
        _print_help(sys.stdout)
        return 0
    return dispatch(rest, home)


if __name__ == "__main__":  # pragma: no cover - exercised through main()
    sys.exit(main())
