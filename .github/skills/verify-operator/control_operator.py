"""Drive `operator` against a disposable operator home, and keep the proof.

It drives the one console script: start, list, attach, stop, recover, handoff,
and doctor. Session handoff is the state worth snapshotting.

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

The console script is invoked as a user invokes it. It resolves the home from
`COPILOT_OPERATOR_HOME`, which this helper sets before the child starts.

`operator handoff` resolves the project from the working directory, so the
front door runs in the registered checkout unless `--cwd` says otherwise.
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
STATE_GLOBS = (
    "operator.log",
    "projects/catalog.csv",
    # The two halves of a handoff. The file is what the next session reads and
    # the marker is what the supervisor polls, so a proof that shows only one
    # of them cannot tell "handed off" from "ended without leaving anything".
    "projects/*/handoff/*.md",
    "restart/*",
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
    print("home is registered and isolated")
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

    for name in ("operator",):
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

    print("doctor:", "healthy" if ok else "NOT healthy")
    return 0 if ok else 1
















def cmd_operator(args) -> int:
    """Drive the `operator` front door.

    The working directory decides the project, so `--cwd` is what makes the
    unregistered refusal drivable through the transcript.
    """
    run = Path(args.run).expanduser().resolve()
    meta = _meta(run)
    argv = [_script("operator"), *args.rest]
    cwd = Path(args.cwd).expanduser().resolve() if args.cwd else Path(meta["repo"])
    return _invoke(run, args.label or "operator " + " ".join(args.rest), argv, cwd)




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

    front = sub.add_parser("operator", help="run the `operator` front door "
                                            "against this run")
    front.add_argument("--run", required=True)
    front.add_argument("--label")
    front.add_argument("--cwd", help="run from here instead of the registered "
                                     "checkout (to drive the unregistered case)")
    front.add_argument("rest", nargs=argparse.REMAINDER)
    front.set_defaults(func=cmd_operator)

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
