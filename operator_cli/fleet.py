"""`operator-fleet` — run the fleet host, and drain the queue it writes.

Two verbs, which are the two halves of `docs/extensions.md` §8's open
assumptions:

``run``
    Poll the ledger, wake the extensions watching the fleet, collect what they
    propose. This is the process that did not exist; `FleetHost.run()` has been
    complete and unreachable since the day it was written.

``proposals``
    Show what is waiting for a human, and with ``--drain``, take it off the
    queue. The queue *refuses* appends past 4 MB rather than rotating, because
    a queue nobody drains that also deletes its own oldest entries is worse
    than one that says it is full -- so an undrained fleet stops accepting
    proposals in a day or two of unattended running. This is the reader that
    stops that happening.

**Draining is a rename, not a read-and-truncate.** `evidence._append` opens the
queue, appends and closes for every record, so a rename between two appends is
safe and an append that races the rename fails closed and is reported. Reading
the file and then truncating it would lose every proposal written in between,
which on a busy fleet is the interesting ones.

Nothing here decides anything about work. A drained proposal is *archived*, not
approved: INV-WORK says approval provenance is mintable only by a human, and
this command has no spelling of approval in it at all -- moving a line from one
file to another cannot become a lease.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path


def _bootstrap() -> None:
    """Put the kernel and fleet directories on `sys.path`.

    The modules in both packages import each other flatly -- `evidence.py` is
    `import evidence`, not `from operator_kernel import evidence` -- so they
    need their *directory* on the path rather than their package. That is not
    an accident of the tests: `extensions.py` spawns its worker as a script
    path specifically so "the kernel directory lands on the child's `sys.path`
    without anyone arranging it", and `tests/op.py` arranges the same thing.

    Located from the installed packages rather than from this file's parents,
    so it works from a checkout and from site-packages alike. Idempotent, and
    it never reorders a path entry that is already there: this runs before
    anything is imported, and a caller who has already arranged their own path
    has a reason.
    """
    import operator_fleet
    import operator_kernel

    for package in (operator_fleet, operator_kernel):
        origin = getattr(package, "__file__", None)
        if not origin:
            continue
        directory = str(Path(origin).resolve().parent)
        if directory not in sys.path:
            sys.path.insert(0, directory)


def _home(given: "str | None") -> Path:
    """Where operator state lives, by the same rule the kernel uses.

    Duplicated from `config.operator_home()` rather than imported, because this
    runs *before* `_bootstrap` has made that importable and because a `--home`
    flag has to win over both. The rule is a documented constant, not logic.
    """
    if given:
        return Path(given).expanduser()
    override = os.environ.get("COPILOT_OPERATOR_HOME")
    return Path(override) if override else Path.home() / ".operator"


def _settle_home(given: "str | None") -> Path:
    """Resolve the home and make every child process agree with it.

    `--home` used to move the ledger and the proposal queue and nothing else.
    Extensions run in spawned workers that inherit this environment and resolve
    the operator home *themselves*, so a relocated fleet read its activation
    config and wrote its extension state under the real `~/.operator` -- either
    staying inert while the given home held an `extensions.json`, or mixing one
    fleet's state into another's. A reviewer found it; the CLI's own tests had
    hidden it by setting the variable and the flag to the same path.

    Exported unconditionally rather than only when `--home` was given, so that
    parent and child are reading the same string rather than independently
    agreeing on a default.
    """
    home = _home(given)
    os.environ["COPILOT_OPERATOR_HOME"] = str(home)
    return home


def _run(args) -> int:
    _bootstrap()
    import fleet_host

    home = _settle_home(args.home)
    fleet = fleet_host.fleet_host(home)
    print(f"fleet host watching {home}")
    if args.rounds:
        print(f"  stopping after {args.rounds} round(s)")
    else:
        print(f"  stop it with: {fleet_host.fleet_stop_marker(home)}")
    try:
        done = fleet.run(rounds=args.rounds, interval=args.interval)
    except KeyboardInterrupt:
        # Caught here rather than around every command. It used to wrap the
        # whole of `main`, which meant a Ctrl-C part-way through a drain --
        # after the queue had been renamed and before it was archived --
        # printed "stopped" and returned 0, reporting success for a batch that
        # was now sitting under a name nothing looked at.
        print("\nstopped")
        return 0
    print(f"fleet host stopped after {done} round(s)")
    return 0


def _read(path: Path) -> "list[dict]":
    """Every parseable record in the queue, with unparseable lines skipped.

    A line that will not parse is *counted* by the caller rather than dropped
    silently -- a queue whose tail is corrupt should not look like a queue
    that is empty, which is this project's oldest failure in its smallest
    possible form.
    """
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return []
    found = []
    for line in raw.splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except ValueError:
            found.append({"_unparseable": line[:200]})
            continue
        found.append(record if isinstance(record, dict)
                     else {"_unparseable": line[:200]})
    return found


def _show(records: "list[dict]", path: Path) -> None:
    for record in records:
        if "_unparseable" in record:
            print(f"  ! unparseable queue line: {record['_unparseable']}")
            continue
        print(f"  [{record.get('ts', '?')}] {record.get('extension', '?')}")
        for line in str(record.get("text", "")).splitlines():
            print(f"      {line}")
        withheld = record.get("withheld")
        if withheld:
            print(f"      (withheld: {', '.join(str(w) for w in withheld)})")


def _claim(path: Path, mine: Path) -> "list[Path]":
    """Take the queue, and adopt any batch a previous drain abandoned.

    Two reviewers found the same two defects in the first version of this, from
    opposite ends. **The queue was read before it was renamed**, so a proposal
    appended in between was archived without ever being printed -- a human
    filing away work they were never shown. And **a batch orphaned by a crash
    between the rename and the archive was never looked at again**: the live
    queue had already been reset, so those proposals were lost to the workflow
    with nothing reporting it.

    So the rename comes first and the reading comes after, and an abandoned
    `proposals.draining.*` file is adopted by *renaming it into this process's
    own batch name*. The rename is the claim: if another drain is genuinely
    mid-flight with that file, the rename fails and it is left alone, which is
    the same mechanism `ledger_tail` relies on and not a lock anyone has to
    remember to take.
    """
    batches: list[Path] = []
    for index, orphan in enumerate(sorted(
            path.parent.glob("proposals.draining.*.jsonl"))):
        if orphan == mine:
            continue
        adopted = mine.with_name(f"{mine.stem}.recovered{index}.jsonl")
        try:
            os.replace(orphan, adopted)
        except OSError:
            continue
        print(f"  recovered an abandoned batch: {orphan.name}")
        batches.append(adopted)
    try:
        os.replace(path, mine)
    except FileNotFoundError:
        return batches
    except OSError as exc:
        print(f"could not take the queue for draining: {exc}", file=sys.stderr)
        raise
    batches.append(mine)
    return batches


def _proposals(args) -> int:
    _bootstrap()
    import fleet_host

    home = _settle_home(args.home)
    path = fleet_host.proposals_path(home)

    if not args.drain:
        records = _read(path)
        if not records:
            print(f"no proposals waiting in {path}")
            return 0
        _show(records, path)
        print(f"{len(records)} proposal(s) in {path}")
        print("  none removed; pass --drain to archive them")
        return 0

    # Process id *and* a nanosecond stamp. A pid alone is reused, and a reused
    # one would have `os.replace` land the live queue on top of a batch a
    # crashed drain had abandoned under the same name -- destroying it, which
    # is the one outcome this whole path exists to prevent.
    mine = path.with_name(
        f"proposals.draining.{os.getpid()}.{time.time_ns()}.jsonl")
    try:
        batches = _claim(path, mine)
    except OSError:
        return 1
    if not batches:
        print(f"no proposals waiting in {path}")
        return 0

    archive = path.with_name("proposals.handled.jsonl")
    shown = 0
    for batch in batches:
        records = _read(batch)
        _show(records, batch)
        shown += len(records)
        try:
            with open(archive, "a", encoding="utf-8") as fh:
                fh.write(batch.read_text(encoding="utf-8"))
            batch.unlink()
        except OSError as exc:
            # The batch is still on disk under its draining name, and the next
            # `--drain` will adopt it. Said out loud anyway: a proposal a human
            # was supposed to see must not go quiet on an error path.
            print(f"batch taken but not archived ({exc}); it is at {batch}",
                  file=sys.stderr)
            return 1
    print(f"archived {shown} proposal(s) to {archive}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="operator-fleet",
        description="Run the fleet host and drain the queue it writes.")
    parser.add_argument("--home", help="operator state directory "
                                       "(default: ~/.operator)")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="poll the ledger and ask the extensions")
    run.add_argument("--rounds", type=int, default=None,
                     help="stop after this many polls (default: run until the "
                          "fleet.stop marker appears)")
    run.add_argument("--interval", type=float, default=None,
                     help="seconds between polls")
    run.set_defaults(func=_run)

    proposals = sub.add_parser("proposals",
                               help="show what is waiting for a human")
    proposals.add_argument("--drain", action="store_true",
                           help="archive the shown proposals and empty the "
                                "queue")
    proposals.set_defaults(func=_proposals)
    return parser


def main(argv: "list[str] | None" = None) -> int:
    parser = build_parser()
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    if getattr(args, "interval", None) is None and args.command == "run":
        _bootstrap()
        import fleet_host
        args.interval = fleet_host.FLEET_POLL_INTERVAL
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover - exercised through main()
    sys.exit(main())
