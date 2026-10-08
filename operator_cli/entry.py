"""`operator` -- the front door.

No arguments and a TTY on stdin and stdout opens a keyboard menu. No TTY
prints help and exits non-zero, so CI cannot hang on a prompt. A verb on the
command line skips the menu.

One console script. Recover and handoff are verbs of it. Handoff stays a
verb: the menu is for a person at the keyboard.
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

from operator_kernel.argtail import at_dashdash

from . import argv as _argv
from . import handoff, messaging, recover
from .home import _bootstrap, _home, _settle_home
from .lifecycle import _same_cwd, attach as _attach, default_name as _default_name, delete as _delete, rename as _rename, start as _start, stop as _stop


from .verbs import VERBS  # noqa: F401

HELP_WORDS = ("-h", "--help", "help")


def _print_help(stream) -> None:
    print("Usage: operator [command]", file=stream)
    print("No command opens a keyboard menu when stdin and stdout are a TTY.", file=stream)
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


class _Actions:
    """The verbs the menu calls. Screens stay free of this wiring."""

    def recoverable_count(self) -> int:
        return len(self.recoverable_names())

    def recoverable_names(self) -> list:
        from supervisor_control import recoverable_instances
        return [inst.display_name for inst in recoverable_instances()]

    def cwd(self) -> str:
        return str(Path.cwd())

    default_name = staticmethod(_default_name)

    def start_problem(self, name: str) -> "str | None":
        """Why the start screen will not start ``name`` here, or None."""
        import operators
        record = operators.find(name)
        if record is None:
            return operators.name_problem(name)
        if _same_cwd(record.cwd, Path.cwd()):
            return None
        return f"{record.name} already works in {record.cwd}. Choose another name."

    def sections(self, base=False):
        """Operators as (running, offline, problems). ``base`` keeps only the
        ones a person started, which are the ones a person may message."""
        import lineage
        from operators import HUMAN

        from .listing import load, sections
        from .menu import Op
        records, problems = load()
        up = lineage.parents(records or [])
        kept = [op for op in records or [] if not base or up[op.id] == HUMAN]
        running, offline = sections(kept)
        return ([Op(op.name, op.cwd, label, True) for op, label in running],
                [Op(op.name, op.cwd, label, False) for op, label in offline],
                problems)

    def base_operators(self):
        running, offline, _ = self.sections(base=True)
        return running, offline

    def waiting_count(self) -> int:
        import mail
        from operators import HUMAN
        return mail.waiting(HUMAN)

    start = staticmethod(_start)
    attach = staticmethod(_attach)
    stop = staticmethod(_stop)
    rename = staticmethod(_rename)
    delete = staticmethod(_delete)
    recover = staticmethod(recover.main)
    send = staticmethod(messaging.send)
    inbox = staticmethod(messaging.inbox)
    message_log = staticmethod(messaging.log_rows)


def _interactive() -> int:
    from .keys import raw_keys
    from .menu import Leave, render, run
    _settle_home(None)
    _bootstrap()
    try:
        with raw_keys() as keys:
            outcome = run(keys, render, _Actions())
        if isinstance(outcome, Leave):
            return outcome.call()
    except KeyboardInterrupt:
        return 130
    return outcome


def _list(_rest: list[str]) -> int:
    _bootstrap()
    from .listing import list_instances
    return list_instances()


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


HANDLERS = {
    "doctor": _doctor,
    "start": _start,
    "list": _list,
    "attach": _attach,
    "stop": _stop,
    "rename": _rename,
    "delete": _delete,
    "recover": recover.main,
    "handoff": handoff.main,
    "send": messaging.send,
    "inbox": messaging.inbox,
}


def dispatch(argv: list[str], home: "str | None" = None) -> int:
    """Run one typed verb, with the home settled first.

    Only typed argv comes through here. The menu calls the same handlers
    through `_Actions`, after `_interactive` has settled the home. A handler's
    SystemExit becomes this function's return code, and in the menu it ends
    the menu instead.

    `recover.py` settles again inside its own `--home`. Settling twice is
    harmless because both resolve and export the same string.
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
            return _interactive()
        _print_help(sys.stderr)
        return 2
    if raw[0] in HELP_WORDS:
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
    if rest[0] in HELP_WORDS:
        _print_help(sys.stdout)
        return 0
    return dispatch(rest, home)


if __name__ == "__main__":  # pragma: no cover - exercised through main()
    sys.exit(main())
