"""`operator-seat` — the commands a seat uses to remember and to recall.

Writing is a command rather than a step at the end of a session, and that is the
whole design rather than a convenience. Of 1,110 recorded session endings, 997
set no restart marker and therefore wrote no handoff; anything persisted at the
end of a session inherits that 90% loss rate and loses precisely the sessions
that ended badly. A `remember` call is durable when it returns.

Reading is a command rather than an injection into the preamble, and that is a
judgement rather than a certainty. Text an agent *fetched* is evidence it went
and got; text sitting in its instructions is posture. The distinction is not
total -- a determined reader can treat either as authority -- but it is the same
one the extension design draws between a claim and a clause, and it costs one
sentence in the preamble instead of a kernel subsystem. `docs/seat-identity.md`
§5 records it as a recommendation, which is what it still is.

Nothing here decides anything. `recall` prints dated, attributed, unverified
claims a previous session made, and the repository remains the only thing in
this system that is actually true.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from .fleet import _bootstrap

#: The seat this is running as, when no `--instance` is given. The preamble
#: tells every session its instance name, so an agent always has one to pass;
#: this is for a human at a prompt who does not want to type it.
INSTANCE_ENV = "OPERATOR_INSTANCE"


def _instance(given: "str | None") -> str:
    if given and given.strip():
        return given.strip()
    return os.environ.get(INSTANCE_ENV, "").strip()


def _named_seat(args) -> "str | None":
    seat = _instance(args.instance)
    if seat:
        return seat
    print(f"no seat named; pass --instance or set {INSTANCE_ENV}",
          file=sys.stderr)
    return None


def _project_ready(cwd: Path, seat: str) -> bool:
    """False after printing why this directory cannot hold a journal."""
    import paths

    found = paths.catalog_guid(cwd)
    if found.undecided:
        print("could not read the project catalog", file=sys.stderr)
        return False
    if found.guid is None:
        print("this directory is not a registered project", file=sys.stderr)
        print("register it with: operator project register", file=sys.stderr)
        return False
    if not paths.guid_is_usable(seat):
        print("the seat name is not usable", file=sys.stderr)
        return False
    return True


def _remember(args) -> int:
    _bootstrap()
    from operator_memory import journal

    seat = _named_seat(args)
    if seat is None:
        return 2
    cwd = Path.cwd()
    if not _project_ready(cwd, seat):
        return 1
    entry_id = journal.remember(cwd, seat, args.kind,
                                " ".join(args.text), session=args.session)
    if entry_id is None:
        path = journal.journal_file(cwd, seat)
        try:
            current = path.stat().st_size if path is not None and path.exists() else 0
        except OSError:
            current = 0
        if current + journal.MAX_TEXT + 128 > journal.MAX_JOURNAL_BYTES:
            print("the journal is at its size limit", file=sys.stderr)
        else:
            print("could not write the journal", file=sys.stderr)
        return 1
    print(f"remembered {entry_id} ({args.kind}) for seat {seat}")
    return 0


def _recall(args) -> int:
    _bootstrap()
    from operator_memory import journal

    seat = _named_seat(args)
    if seat is None:
        return 2
    cwd = Path.cwd()
    if not _project_ready(cwd, seat):
        return 1
    entries = journal.recall(cwd, seat, per_kind=args.per_kind)
    if not entries:
        print(f"seat {seat} has nothing recorded for this project")
        return 0
    text, withheld = journal.render(seat, entries)
    print(f"{len(entries)} entr(ies) recorded by seat {seat} in earlier "
          f"sessions. These are claims that seat made about the past, not "
          f"statements about the present - check anything you rely on.")
    print(text)
    if withheld:
        # Said out loud, because a clause silently replaced by the refusal text
        # is a seat wondering why its own note reads strangely.
        print(f"\n{len(withheld)} entr(ies) had wording withheld: an entry may "
              f"not grant authority, including to the seat that wrote it.",
              file=sys.stderr)
    return 0


def _forget(args) -> int:
    _bootstrap()
    from operator_memory import journal

    seat = _named_seat(args)
    if seat is None:
        return 2
    cwd = Path.cwd()
    if not _project_ready(cwd, seat):
        return 1
    if journal.forget(cwd, seat, args.id, session=args.session):
        print(f"entry {args.id} will no longer be recalled "
              f"(superseded, not deleted)")
        return 0
    print("nothing written", file=sys.stderr)
    return 1


def _seat_flags(parser: argparse.ArgumentParser, *, on_subcommand: bool) -> None:
    extra = {"default": argparse.SUPPRESS} if on_subcommand else {}
    parser.add_argument("--instance",
                        help="the seat (default: $" + INSTANCE_ENV + ")",
                        **({"default": None} | extra))
    parser.add_argument("--session", type=int,
                        help="the session number writing this",
                        **({"default": 0} | extra))


def build_parser(prog: str = "operator-seat") -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=prog,
        description="What this seat remembers about itself, in this project.")
    _seat_flags(parser, on_subcommand=False)
    common = argparse.ArgumentParser(add_help=False)
    _seat_flags(common, on_subcommand=True)
    sub = parser.add_subparsers(dest="command", required=True)

    remember = sub.add_parser("remember", parents=[common],
                              help="record one claim, durably, now")
    remember.add_argument("--kind", required=True,
                          choices=("decision", "gotcha", "disposition",
                                   "attempt"))
    remember.add_argument("text", nargs="+")
    remember.set_defaults(func=_remember)

    recall = sub.add_parser("recall", parents=[common],
                            help="what earlier sessions recorded")
    recall.add_argument("--per-kind", type=int, default=5)
    recall.set_defaults(func=_recall)

    forget = sub.add_parser("forget", parents=[common],
                            help="stop recalling one entry")
    forget.add_argument("id")
    forget.set_defaults(func=_forget)
    return parser


def main(argv: "list[str] | None" = None, prog: str = "operator-seat") -> int:
    parser = build_parser(prog)
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover - exercised through main()
    sys.exit(main())
