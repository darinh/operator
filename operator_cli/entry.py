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
import sys
from dataclasses import dataclass

from . import fleet, recover, seat
from .fleet import _bootstrap, _home, _settle_home


@dataclass(frozen=True)
class Verb:
    tokens: tuple[str, ...]
    help: str
    menu: str
    prompts: tuple[str, ...] = ()


@dataclass(frozen=True)
class Item:
    label: str
    argv: tuple[str, ...]
    prompts: tuple[str, ...] = ()


VERBS: tuple[Verb, ...] = (
    Verb(("start",), "start a supervised seat in the background",
         "Start a supervised seat", ("name",)),
    Verb(("list",), "list running seats",
         "List running seats"),
    Verb(("join",), "attach this terminal to a running seat",
         "Join a running seat", ("name",)),
    Verb(("stop",), "ask a seat's supervisor to stop",
         "Stop a supervised seat", ("name",)),
    Verb(("restart-loop",),
         "replace a supervisor without stopping the session",
         "Restart one seat's supervisor", ("name",)),
    Verb(("recover",), "bring back seats lost to a crash or reboot",
         "Recover seats after a crash"),
    Verb(("remember",), "record one claim for this seat",
         "Remember something about this seat",
         ("instance", "kind", "text")),
    Verb(("recall",), "show what earlier sessions recorded",
         "Recall what this seat recorded", ("instance",)),
    Verb(("forget",), "stop recalling one journal entry",
         "Forget one journal entry", ("instance", "id")),
    Verb(("fleet", "run"), "poll the ledger and ask the extensions",
         "Run the fleet host"),
    Verb(("fleet", "proposals"), "show what is waiting for a human",
         "Show fleet proposals"),
    Verb(("trace",), "show recent ledger records, newest first",
         "Show recent ledger records"),
    Verb(("verify",), "check the ledger chain",
         "Verify the ledger chain"),
)

_PROMPTS = {
    "name": "Seat name: ",
    "instance": "Seat name: ",
    "kind": "Kind (decision, gotcha, disposition, attempt): ",
    "text": "Text: ",
    "id": "Entry id: ",
}


def menu_items() -> tuple[Item, ...]:
    items = []
    for verb in VERBS:
        items.append(Item(verb.menu, verb.tokens, verb.prompts))
        if verb.tokens == ("restart-loop",):
            items.append(Item(
                "Restart every running supervisor",
                ("restart-loop", "--all")))
    return tuple(items)


def _print_help(stream) -> None:
    print("Usage: operator [command]", file=stream)
    print("No command opens a menu when stdin and stdout are a TTY.",
          file=stream)
    print(file=stream)
    for verb in VERBS:
        print(f"  {' '.join(verb.tokens):<22}{verb.help}", file=stream)


def _peel_home(argv: list[str]) -> tuple["str | None", list[str]]:
    home, rest = None, []
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--home":
            if i + 1 >= len(argv):
                print("operator --home needs a directory", file=sys.stderr)
                raise SystemExit(2)
            home = argv[i + 1]
            i += 2
            continue
        if arg.startswith("--home="):
            home = arg.split("=", 1)[1]
            i += 1
            continue
        rest.append(arg)
        i += 1
    return home, rest


def _prep() -> None:
    _bootstrap()


def _ask(prompt: str) -> str:
    try:
        return input(prompt).strip()
    except EOFError:
        print()
        return ""


def _build_argv(item: Item, values: dict[str, str]) -> list[str]:
    argv = list(item.argv)
    if "instance" in values:
        argv = [argv[0], "--instance", values["instance"], *argv[1:]]
    if "name" in values:
        if item.argv[0] == "start":
            argv += ["--name", values["name"]]
        else:
            argv += [values["name"]]
    if "kind" in values:
        argv += ["--kind", values["kind"]]
    if "text" in values:
        argv += [values["text"]]
    if "id" in values:
        argv += [values["id"]]
    return argv


def _menu() -> int:
    items = menu_items()
    print("What do you want to do?")
    print()
    for index, item in enumerate(items, 1):
        print(f"  {index}. {item.label}")
    print("  0. Quit")
    print()
    choice = _ask("Choice: ")
    if choice in ("", "0", "q", "Q"):
        return 0 if choice in ("0", "q", "Q") else 2
    try:
        number = int(choice)
    except ValueError:
        print("Not a number.", file=sys.stderr)
        return 2
    if number < 1 or number > len(items):
        print("No such choice.", file=sys.stderr)
        return 2
    item = items[number - 1]
    values = {}
    for key in item.prompts:
        value = _ask(_PROMPTS[key])
        if not value:
            print(f"A {key} is needed.", file=sys.stderr)
            return 2
        values[key] = value
    argv = _build_argv(item, values)
    print("Running: operator " + " ".join(argv))
    return dispatch(argv)


def _start(rest: list[str]) -> int:
    _prep()
    name, fresh, copilot = "", False, []
    i = 0
    while i < len(rest):
        arg = rest[i]
        if arg in ("-h", "--help"):
            print("Usage: operator start --name NAME [--fresh] "
                  "[copilot-args...]")
            return 0
        if arg == "--fresh":
            fresh = True
        elif arg == "--name":
            i += 1
            if i >= len(rest) or not rest[i].strip():
                print("operator start --name needs a value", file=sys.stderr)
                return 2
            name = rest[i]
        elif arg.startswith("--name="):
            name = arg.split("=", 1)[1]
        else:
            copilot.append(arg)
        i += 1
    if not name.strip():
        print("Usage: operator start --name NAME [--fresh] [copilot-args...]",
              file=sys.stderr)
        return 2
    from instance import Instance
    from supervisor import _spawn_background_loop
    pid = _spawn_background_loop(Instance(name), copilot, is_fresh=fresh)
    print(f"started {name} (pid {pid})")
    return 0


def _list(_rest: list[str]) -> int:
    _prep()
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
    _prep()
    name = _named(rest)
    if not name:
        print("Usage: operator join NAME", file=sys.stderr)
        return 2
    from config import MUX
    from instance import Instance
    inst = Instance(name)
    if not MUX.has_session(inst.session):
        print(f"No running seat '{name}'.", file=sys.stderr)
        return 1
    return MUX.attach(inst.session)


def _stop(rest: list[str]) -> int:
    _prep()
    name = _named(rest)
    if not name:
        print("Usage: operator stop NAME", file=sys.stderr)
        return 2
    from instance import Instance
    from supervisor_control import _request_supervisor_stop
    _request_supervisor_stop(Instance(name))
    print(f"stop requested for {name}")
    return 0


def _restart_loop(rest: list[str]) -> int:
    _prep()
    from supervisor_control import restart_all_loops, restart_loop
    if "--all" in rest:
        return restart_all_loops()
    name = _named(rest)
    return restart_loop(name or None)


def _recover(rest: list[str]) -> int:
    return recover.main(rest)


def _seat_cmd(verb: str, rest: list[str]) -> int:
    parent: list[str] = []
    child: list[str] = []
    i = 0
    while i < len(rest):
        arg = rest[i]
        if arg in ("--instance", "--session"):
            if i + 1 >= len(rest):
                print(f"operator {verb} {arg} needs a value", file=sys.stderr)
                return 2
            parent += [arg, rest[i + 1]]
            i += 2
            continue
        if arg.startswith("--instance=") or arg.startswith("--session="):
            parent.append(arg)
            i += 1
            continue
        child.append(arg)
        i += 1
    return seat.main([*parent, verb, *child])


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


def _trace(rest: list[str]) -> int:
    _prep()
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
    path = _home(None) / "trace.jsonl"
    if not path.exists() and not path.with_suffix(path.suffix + ".1").exists():
        print(f"no ledger at {path}")
        return 0
    tail = ledger_tail.LedgerTail(path, state=None)
    records: list[dict] = []
    while True:
        batch = tail.read()
        if not batch:
            break
        records.extend(batch)
    records.reverse()
    for record in records[:n]:
        print(json.dumps(record, ensure_ascii=True, default=str))
    return 0


def _verify(_rest: list[str]) -> int:
    _prep()
    import ledger_chain
    path = _home(None) / "trace.jsonl"
    rotated = path.with_suffix(path.suffix + ".1")
    paths = [p for p in (rotated, path) if p.exists()]
    result = ledger_chain.verify(paths)
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
    "start": _start,
    "list": _list,
    "join": _join,
    "stop": _stop,
    "restart-loop": _restart_loop,
    "recover": _recover,
    "remember": _remember,
    "recall": _recall,
    "forget": _forget,
    "fleet": _fleet,
    "trace": _trace,
    "verify": _verify,
}


def dispatch(argv: list[str]) -> int:
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
        if sys.stdin.isatty() and sys.stdout.isatty():
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
    _settle_home(home)
    if not rest:
        _print_help(sys.stderr)
        return 2
    return dispatch(rest)


if __name__ == "__main__":  # pragma: no cover - exercised through main()
    sys.exit(main())
