"""Drive the operator CLIs against a disposable operator home, and keep the proof.

**Nothing here knows about any particular extension.** It drives what every
operator install has: the two console scripts, the ledger, the proposal queue,
the seat journal, and the activation file. `enable` takes any extension by name
and `seed-ledger` takes any record shape, so an install with no extensions at all
is fully drivable and an extension this file has never heard of needs no change
to it.

Everything a run creates lives under one run directory. `up` builds it, every
other verb addresses it with `--run`, and `down` removes the instance while
leaving the artifacts behind.

Run directory layout::

    <root>/<run-id>/
        home/        the isolated operator home  (removed by `down`)
        artifacts/   transcripts and state snapshots  (survives `down`)

**Isolation is the reason this exists, and it is load-bearing.** Every command
sets ``COPILOT_OPERATOR_HOME`` *before* spawning a fresh process, so the kernel's
import-time ``config.OPERATOR_HOME`` resolves to the run's home in the child.
That ordering is not incidental: the constant is captured at import, so code that
sets the variable *after* importing the kernel -- as the in-process test suite
does -- writes to the developer's real ``~/.operator`` instead. `doctor` asserts
the home is not the real one, and a run of every verb here leaves the real
``~/.operator`` byte-for-byte unchanged.

The console scripts are invoked as a user invokes them. `operator-fleet` takes
`--home`; `operator-seat` has no such flag and resolves the home only from the
environment, so both are set on every call and the two agree.

Seat commands run with the repository as the working directory on purpose:
`journal.remember` resolves the project from `Path.cwd()` through the catalog, so
the same command from elsewhere writes nothing and reports that it wrote nothing.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

#: Files under the home worth snapshotting. Globs, resolved against the home.
#: Extension state is matched by wildcard rather than by name, so an extension
#: this file has never heard of still has its state captured.
STATE_GLOBS = (
    "trace.jsonl",
    "proposals.jsonl",
    "proposals.handled.jsonl",
    "fleet-failures.jsonl",
    "fleet-tail.json",
    "extensions.json",
    "operator.log",
    "extensions/*.json",
    "projects/catalog.csv",
    "projects/*/journal/*.jsonl",
)


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def repo_root(start: Path) -> Path:
    """The primary checkout, by the rule `paths.primary_repo_root` uses.

    `git rev-parse --show-toplevel` is wrong inside a linked worktree -- it
    answers the worktree -- and the catalog is keyed on the primary checkout. The
    first record of `git worktree list --porcelain` is the primary one and reads
    the same from anywhere in the repository.
    """
    out = subprocess.run(
        ["git", "-C", str(start), "worktree", "list", "--porcelain"],
        capture_output=True, encoding="utf-8", errors="replace", timeout=30)
    for line in (out.stdout or "").splitlines():
        if line.startswith("worktree "):
            return Path(line[len("worktree "):].strip()).resolve()
    raise SystemExit(f"not a git checkout: {start}")


def _home(run: Path) -> Path:
    return run / "home"


def _artifacts(run: Path) -> Path:
    return run / "artifacts"


def _meta(run: Path) -> dict:
    path = run / "run.json"
    if not path.exists():
        raise SystemExit(f"no run at {run}; create one with `up` first")
    return json.loads(path.read_text(encoding="utf-8"))


def _env(run: Path) -> dict:
    """The child's environment, with the home settled before the process starts."""
    env = dict(os.environ)
    env["COPILOT_OPERATOR_HOME"] = str(_home(run))
    return env


def _script(name: str) -> str:
    found = shutil.which(name)
    if not found:
        raise SystemExit(
            f"{name} is not on PATH. Install the package first: pip install -e .")
    return found


def _record(run: Path, label: str, argv: list, proc) -> None:
    """Append one command to the transcript and write its streams beside it."""
    art = _artifacts(run)
    art.mkdir(parents=True, exist_ok=True)
    body = (f"\n## {label}  ({_utcnow()})\n\n"
            f"```\n$ {' '.join(argv)}\nexit {proc.returncode}\n```\n\n"
            f"stdout:\n```\n{(proc.stdout or '').rstrip()}\n```\n")
    if (proc.stderr or "").strip():
        body += f"\nstderr:\n```\n{proc.stderr.rstrip()}\n```\n"
    with open(art / "transcript.md", "a", encoding="utf-8") as fh:
        fh.write(body)


def _invoke(run: Path, label: str, argv: list, cwd: Path) -> int:
    proc = subprocess.run(argv, cwd=str(cwd), env=_env(run), capture_output=True,
                          encoding="utf-8", errors="replace", timeout=300)
    _record(run, label, argv, proc)
    sys.stdout.write(proc.stdout or "")
    sys.stderr.write(proc.stderr or "")
    return proc.returncode


# ── verbs ────────────────────────────────────────────────────────────────


def cmd_up(args) -> int:
    root = Path(args.root).expanduser() if args.root else Path.cwd() / ".verify-operator"
    run = root / (args.run_id or datetime.now().strftime("%Y%m%d-%H%M%S-")
                  + uuid.uuid4().hex[:6])
    repo = repo_root(Path.cwd())
    guid = str(uuid.uuid4())

    home = _home(run)
    (home / "projects" / guid).mkdir(parents=True, exist_ok=True)
    _artifacts(run).mkdir(parents=True, exist_ok=True)

    # The catalog is the only thing that makes this directory a "project", and
    # the path column is quoted because a checkout path may contain a comma.
    # It lives inside the disposable home, so `down` takes it with everything
    # else -- no project is registered anywhere global.
    catalog = home / "projects" / "catalog.csv"
    catalog.write_text(f'"{repo}",{guid}\n', encoding="utf-8")
    (run / "run.json").write_text(json.dumps({
        "created": _utcnow(), "repo": str(repo), "guid": guid,
        "home": str(home), "artifacts": str(_artifacts(run)),
    }, indent=2), encoding="utf-8")

    print(f"run        {run}")
    print(f"home       {home}")
    print(f"artifacts  {_artifacts(run)}")
    print(f"project    {repo}  ->  {guid}")
    print("no extensions are enabled: this is a core-only instance")
    return 0


def cmd_doctor(args) -> int:
    """Read-only: is this instance worth driving? Nothing here mutates state."""
    run = Path(args.run).expanduser().resolve()
    ok = True

    def check(label: str, passed: bool, detail: str = "") -> None:
        nonlocal ok
        ok = ok and passed
        print(f"  {'PASS' if passed else 'FAIL'}  {label}"
              + (f"  [{detail}]" if detail else ""))

    meta_path = run / "run.json"
    check("run directory exists", meta_path.exists(), str(run))
    if not meta_path.exists():
        return 1
    meta = json.loads(meta_path.read_text(encoding="utf-8"))

    for name in ("operator-fleet", "operator-seat"):
        found = shutil.which(name)
        check(f"{name} on PATH", found is not None, found or "missing")

    # The installed package must be THIS checkout, or the run proves something
    # about whatever else is on sys.path.
    try:
        import operator_kernel
        from operator_kernel import version
        installed = Path(operator_kernel.__file__).resolve().parent.parent
        check("installed package is this checkout",
              installed == Path(meta["repo"]).resolve(), str(installed))
        check("kernel version readable", bool(version.__version__),
              version.__version__)
    except Exception as exc:
        check("operator_kernel importable", False, repr(exc))

    home = Path(meta["home"])
    check("isolated home exists", home.is_dir(), str(home))
    check("home is not the real ~/.operator",
          home.resolve() != (Path.home() / ".operator").resolve())
    catalog = home / "projects" / "catalog.csv"
    check("catalog registers the repo",
          catalog.exists() and meta["guid"] in catalog.read_text(encoding="utf-8"))

    # Reported, never required. A core-only run has none of these, on purpose.
    config = home / "extensions.json"
    enabled = []
    if config.exists():
        try:
            data = json.loads(config.read_text(encoding="utf-8"))
            enabled = [k for k, v in data.items()
                       if isinstance(v, dict) and v.get("enabled") is True]
        except ValueError:
            enabled = ["<config unreadable: everything is off>"]
    print(f"  ----  extensions enabled: {', '.join(enabled) or 'none (core only)'}")
    print("doctor:", "healthy" if ok else "NOT healthy")
    return 0 if ok else 1


def cmd_enable(args) -> int:
    """Turn one extension on in this run's home, by name. Extensions ship inert.

    Generic on purpose: this knows nothing about which extensions exist, what
    settings they take, or what they are expected to do. Writing the activation
    file is core kernel behaviour; the meaning of an entry is the extension's.
    """
    run = Path(args.run).expanduser().resolve()
    path = _home(run) / "extensions.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    config = {}
    if path.exists():
        try:
            config = json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            config = {}
    entry = {"enabled": True}
    for pair in args.setting or []:
        key, _, value = pair.partition("=")
        try:
            entry[key] = json.loads(value)
        except ValueError:
            entry[key] = value
    config[args.extension] = entry
    path.write_text(json.dumps(config, indent=2), encoding="utf-8")
    print(f"enabled {args.extension} in {path}: {json.dumps(entry)}")
    return 0


def cmd_seed_ledger(args) -> int:
    """Append records to the ledger. Their shape is the caller's business.

    The ledger is core -- `evidence.py` writes it and the fleet host tails it --
    but no record shape is privileged here. An extension that reads a particular
    event is verified by seeding that event from its own recipe, which keeps the
    extension's schema in the extension's documentation rather than in this file.
    """
    run = Path(args.run).expanduser().resolve()
    records = []
    if args.records:
        for line in Path(args.records).read_text(encoding="utf-8").splitlines():
            if line.strip():
                records.append(json.loads(line))
    for blob in args.record or []:
        records.append(json.loads(blob))
    if not records:
        raise SystemExit("nothing to seed; pass --record or --records")

    trace = _home(run) / "trace.jsonl"
    trace.parent.mkdir(parents=True, exist_ok=True)
    with open(trace, "a", encoding="utf-8") as fh:
        for record in records:
            record.setdefault("ts", _utcnow())
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    print(f"seeded {len(records)} record(s) into {trace}")
    return 0


def cmd_seed_queue(args) -> int:
    """Append records to the proposal queue, so the drain is provable core-only.

    Fixture setup, exactly as `seed-ledger` is. Only an extension *produces* a
    proposal, so without this the whole `proposals --drain` path -- the rename,
    the archive, the recovery of an abandoned batch -- would be unverifiable on
    an install with no extensions enabled. The behaviour under test is the
    draining, not the producing.

    ``--abandoned`` writes the batch under a ``proposals.draining.*`` name
    instead of the live queue, which is the state a drain that died between its
    rename and its archive leaves behind. That batch is supposed to be adopted
    by the next drain, and there is no other way to reach that path: it needs a
    crash at a one-instruction window.
    """
    run = Path(args.run).expanduser().resolve()
    records = []
    for blob in args.record or []:
        records.append(json.loads(blob))
    if not records:
        records = [{"extension": args.extension,
                    "text": f"[extension {args.extension}, unverified] "
                            f"seeded proposal for verification"}]

    home = _home(run)
    if args.abandoned:
        # A pid and a nanosecond stamp, the same shape `_claim` writes, so the
        # adopting drain treats it as a genuine orphan rather than a special
        # case. The uniqueness loop is not decoration: `time.time_ns()` is
        # coarse enough on Windows that two calls in the same millisecond
        # return the same value, and two orphans then land in one file, which
        # is the opposite of the case this is meant to set up. `_claim` itself
        # is safe without it because it runs once per drain process.
        while True:
            target = home / (f"proposals.draining.{os.getpid()}."
                             f"{time.time_ns()}.jsonl")
            if not target.exists():
                break
    else:
        target = home / "proposals.jsonl"
    target.parent.mkdir(parents=True, exist_ok=True)
    with open(target, "a", encoding="utf-8") as fh:
        for record in records:
            record.setdefault("ts", _utcnow())
            record.setdefault("extension", args.extension)
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")

    if args.pad_to_bytes:
        # `FleetHost._append_proposal` compares `st_size` against
        # MAX_QUEUE_BYTES, so bulk is all that is needed to reach the refusal.
        # Padding is one long record rather than many, to keep the file cheap
        # to write and to leave the record count readable.
        while target.stat().st_size < args.pad_to_bytes:
            short = args.pad_to_bytes - target.stat().st_size
            filler = {"ts": _utcnow(), "extension": args.extension,
                      "text": "x" * max(1, min(short, 1 << 20))}
            with open(target, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(filler, ensure_ascii=False) + "\n")
        print(f"padded {target.name} to {target.stat().st_size}b")

    print(f"seeded {len(records)} proposal(s) into {target}")
    return 0


def cmd_rotate_ledger(args) -> int:
    """Rotate `trace.jsonl` by rename, exactly as `evidence._rotate_if_needed` does.

    Fixture setup. The real rotation fires at 8 MB, which is a slow and clumsy
    thing to reach through a CLI, and the behaviour actually under test is the
    *tail's*: it must follow the records into `trace.jsonl.1` and then come back
    to the new file, losing and duplicating nothing. Renaming is the whole of
    what the appender does, so a rename here reaches the same code path.

    The rename is why the cursor cannot be keyed on size: the replacement file
    can be longer than the offset, and a size check would see nothing wrong.
    """
    run = Path(args.run).expanduser().resolve()
    trace = _home(run) / "trace.jsonl"
    if not trace.exists():
        raise SystemExit(f"nothing to rotate: {trace} does not exist")
    rotated = trace.with_name("trace.jsonl.1")
    if rotated.exists():
        rotated.unlink()
    size = trace.stat().st_size
    os.replace(trace, rotated)
    print(f"rotated {trace.name} ({size}b) -> {rotated.name}")
    return 0


def cmd_seed_journal(args) -> int:
    """Pad a seat's journal with well-formed entries, to reach the refusal size.

    Fixture setup. `journal.remember` compares the *resulting* size against
    MAX_JOURNAL_BYTES, so the only way to see it refuse is to arrive with a
    journal already near the limit — and getting there honestly means tens of
    thousands of `operator-seat remember` calls.

    The entries written are the shape `remember` writes, so `recall` reads them
    rather than skipping them as unparseable: a journal padded with junk would
    prove a refusal caused by the wrong thing.
    """
    run = Path(args.run).expanduser().resolve()
    meta = _meta(run)
    path = (_home(run) / "projects" / meta["guid"] / "journal"
            / f"{args.seat}.jsonl")
    path.parent.mkdir(parents=True, exist_ok=True)

    written = 0
    # The size is counted rather than stat'd: the handle is buffered, so
    # `stat()` lags the writes and the loop overshoots by whatever sits in the
    # buffer. `newline=""` then stops Windows translating each "\n" into
    # "\r\n", which otherwise adds one byte per line that the count does not
    # see -- measured at 5,751 bytes past a 4,193,000 target, enough to carry a
    # journal over a limit the pad was meant to stop just short of.
    size = path.stat().st_size if path.exists() else 0
    with open(path, "a", encoding="utf-8", newline="") as fh:
        while True:
            record = {
                "ts": _utcnow(), "id": f"{written:08x}", "instance": args.seat,
                "session": 0, "kind": "gotcha",
                "text": "padding written by verify-operator " + "x" * 540,
                "verified": False, "supersedes": [],
            }
            line = json.dumps(record, ensure_ascii=False) + "\n"
            encoded = len(line.encode("utf-8"))
            if size + encoded > args.pad_to_bytes:
                break
            fh.write(line)
            size += encoded
            written += 1
    print(f"padded {path.name} to {path.stat().st_size}b with {written} entries")
    return 0


def cmd_fleet(args) -> int:
    run = Path(args.run).expanduser().resolve()
    argv = [_script("operator-fleet"), "--home", str(_home(run)), *args.rest]
    return _invoke(run, args.label or "fleet " + " ".join(args.rest), argv,
                   Path.cwd())


def cmd_seat(args) -> int:
    run = Path(args.run).expanduser().resolve()
    meta = _meta(run)
    argv = [_script("operator-seat"), *args.rest]
    # cwd is the registered checkout: the journal is resolved from it. `--cwd`
    # overrides it so the "this directory is not a registered project" refusal
    # is drivable through the transcript rather than by a raw call beside it.
    cwd = Path(args.cwd).expanduser().resolve() if args.cwd else Path(meta["repo"])
    return _invoke(run, args.label or "seat " + " ".join(args.rest), argv, cwd)


def cmd_gate(args) -> int:
    """Ask the kernel's launch gate whether a seat may start, and report.

    The one hook the two console scripts cannot reach. `admit_launch` is a
    *kernel* hook on the seat launch path, and `operator-fleet` lists it at
    discovery without ever calling it -- the fleet hooks and the kernel hooks
    are disjoint sets. That made it the last unverifiable thing in the map.

    It is reachable without a supervisor, though: `extension_seam.launch_gate`
    is the factory the supervisor itself uses, and `admits()` takes plain
    values. So this drives the real gate, the real discovery and the real
    extension, and only the loop around them is absent.

    Run in a child process, like every other verb here, so the kernel resolves
    `OPERATOR_HOME` from the environment at import and this run's home is the
    one it sees.
    """
    run = Path(args.run).expanduser().resolve()
    # The checkout recorded at `up`, not the current directory: every other
    # verb works from anywhere, and locating the kernel by `Path.cwd()` made
    # this one silently depend on where it was called from. A test that
    # chdir'd elsewhere failed on correct code, which is how it was found.
    kernel = Path(_meta(run)["repo"]) / "operator_kernel"
    script = (
        "import os, sys\n"
        "sys.path.insert(0, sys.argv[1])\n"
        "import extension_seam\n"
        "gate = extension_seam.launch_gate(home=sys.argv[2])\n"
        "a = gate.admits(instance=sys.argv[3], session=int(sys.argv[4]),\n"
        "                workdir=sys.argv[5])\n"
        "print('admit:', a.admit)\n"
        "for name, reason in a.refusals:\n"
        "    print(f'refused by {name}: {reason}')\n"
        "for name, error in a.blind:\n"
        "    print(f'blind {name}: {error}')\n"
    )
    argv = [sys.executable, "-c", script, str(kernel), str(_home(run)),
            args.instance, str(args.session), str(Path(args.workdir).resolve())]
    return _invoke(run, args.label or f"gate {args.instance}", argv, Path.cwd())


def cmd_evidence(args) -> int:
    """Copy the home's state files into the artifacts directory under a label."""
    run = Path(args.run).expanduser().resolve()
    home = _home(run)
    dest = _artifacts(run) / args.label
    dest.mkdir(parents=True, exist_ok=True)
    copied = []
    for pattern in STATE_GLOBS:
        for src in sorted(home.glob(pattern)):
            if not src.is_file():
                continue
            target = dest / src.relative_to(home).as_posix().replace("/", "__")
            shutil.copy2(src, target)
            copied.append(f"{src.relative_to(home).as_posix()} ({src.stat().st_size}b)")
    (dest / "MANIFEST.txt").write_text(
        f"captured {_utcnow()} from {home}\n\n" + "\n".join(copied) + "\n",
        encoding="utf-8")
    print(f"captured {len(copied)} state file(s) to {dest}")
    for line in copied:
        print(f"  {line}")
    return 0


def cmd_down(args) -> int:
    """Remove the instance. Idempotent, and it never touches the artifacts."""
    run = Path(args.run).expanduser().resolve()
    home = _home(run)
    if home.exists():
        shutil.rmtree(home, ignore_errors=True)
        print(f"removed {home}")
    else:
        print(f"already removed {home}")
    art = _artifacts(run)
    print(f"artifacts kept at {art}"
          if art.exists() else f"no artifacts at {art}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="control_operator.py",
        description="Drive the operator CLIs against a disposable operator home.")
    sub = parser.add_subparsers(dest="command", required=True)

    up = sub.add_parser("up", help="create an isolated home and register the repo")
    up.add_argument("--root", help="where run directories live "
                                   "(default: ./.verify-operator)")
    up.add_argument("--run-id", help="name this run (default: timestamp+random)")
    up.set_defaults(func=cmd_up)

    doctor = sub.add_parser("doctor", help="read-only health check of one run")
    doctor.add_argument("--run", required=True)
    doctor.set_defaults(func=cmd_doctor)

    enable = sub.add_parser("enable", help="turn any extension on, by name")
    enable.add_argument("--run", required=True)
    enable.add_argument("--extension", required=True)
    enable.add_argument("--setting", action="append",
                        help="extra key=value, JSON-parsed (repeatable)")
    enable.set_defaults(func=cmd_enable)

    seed = sub.add_parser("seed-ledger",
                          help="append records of any shape to the ledger")
    seed.add_argument("--run", required=True)
    seed.add_argument("--record", action="append",
                      help="one JSON object (repeatable)")
    seed.add_argument("--records", help="a .jsonl file of records")
    seed.set_defaults(func=cmd_seed_ledger)

    queue = sub.add_parser("seed-queue",
                           help="append proposals to the queue (fixture setup)")
    queue.add_argument("--run", required=True)
    queue.add_argument("--extension", default="verify-fixture",
                       help="attributed source (default: verify-fixture)")
    queue.add_argument("--record", action="append",
                       help="one JSON object (repeatable)")
    queue.add_argument("--abandoned", action="store_true",
                       help="write it as a proposals.draining.* batch, as a "
                            "crashed drain would leave behind")
    queue.add_argument("--pad-to-bytes", type=int,
                       help="pad the queue to at least N bytes, to reach the "
                            "size at which the host refuses to append")
    queue.set_defaults(func=cmd_seed_queue)

    rotate = sub.add_parser("rotate-ledger",
                            help="rename trace.jsonl to trace.jsonl.1 (fixture)")
    rotate.add_argument("--run", required=True)
    rotate.set_defaults(func=cmd_rotate_ledger)

    journal = sub.add_parser("seed-journal",
                             help="pad a seat's journal toward its size cap "
                                  "(fixture)")
    journal.add_argument("--run", required=True)
    journal.add_argument("--seat", required=True)
    journal.add_argument("--pad-to-bytes", type=int, required=True)
    journal.set_defaults(func=cmd_seed_journal)

    gate = sub.add_parser("gate", help="ask the kernel launch gate about a seat")
    gate.add_argument("--run", required=True)
    gate.add_argument("--label")
    gate.add_argument("--instance", default="verify-seat")
    gate.add_argument("--session", type=int, default=1)
    gate.add_argument("--workdir", required=True,
                      help="the repository the seat would work in")
    gate.set_defaults(func=cmd_gate)

    fleet = sub.add_parser("fleet", help="run operator-fleet against this run")
    fleet.add_argument("--run", required=True)
    fleet.add_argument("--label")
    fleet.add_argument("rest", nargs=argparse.REMAINDER)
    fleet.set_defaults(func=cmd_fleet)

    seat = sub.add_parser("seat", help="run operator-seat against this run")
    seat.add_argument("--run", required=True)
    seat.add_argument("--label")
    seat.add_argument("--cwd", help="run from here instead of the registered "
                                    "checkout (to drive the unregistered case)")
    seat.add_argument("rest", nargs=argparse.REMAINDER)
    seat.set_defaults(func=cmd_seat)

    evidence = sub.add_parser("evidence", help="snapshot home state into artifacts")
    evidence.add_argument("--run", required=True)
    evidence.add_argument("--label", required=True)
    evidence.set_defaults(func=cmd_evidence)

    down = sub.add_parser("down", help="remove the instance, keep the artifacts")
    down.add_argument("--run", required=True)
    down.set_defaults(func=cmd_down)
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(sys.argv[1:] if argv is None else argv)
    rest = getattr(args, "rest", None)
    if rest and rest[0] == "--":
        args.rest = rest[1:]
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
