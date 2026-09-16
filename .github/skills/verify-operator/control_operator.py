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
    """
    run = Path(args.run).expanduser().resolve()
    records = []
    for blob in args.record or []:
        records.append(json.loads(blob))
    if not records:
        records = [{"extension": args.extension,
                    "text": f"[extension {args.extension}, unverified] "
                            f"seeded proposal for verification"}]

    queue = _home(run) / "proposals.jsonl"
    queue.parent.mkdir(parents=True, exist_ok=True)
    with open(queue, "a", encoding="utf-8") as fh:
        for record in records:
            record.setdefault("ts", _utcnow())
            record.setdefault("extension", args.extension)
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    print(f"seeded {len(records)} proposal(s) into {queue}")
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
    # cwd is the registered checkout: the journal is resolved from it.
    return _invoke(run, args.label or "seat " + " ".join(args.rest), argv,
                   Path(meta["repo"]))


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
    queue.set_defaults(func=cmd_seed_queue)

    fleet = sub.add_parser("fleet", help="run operator-fleet against this run")
    fleet.add_argument("--run", required=True)
    fleet.add_argument("--label")
    fleet.add_argument("rest", nargs=argparse.REMAINDER)
    fleet.set_defaults(func=cmd_fleet)

    seat = sub.add_parser("seat", help="run operator-seat against this run")
    seat.add_argument("--run", required=True)
    seat.add_argument("--label")
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
