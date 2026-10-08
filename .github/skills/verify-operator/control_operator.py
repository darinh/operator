"""Drive `operator` against a disposable instance, and keep the proof.

`up` builds everything a run needs under one run directory. Every other verb
addresses it with `--run`. `down` stops what the run started, removes the
instance, and leaves the artifacts::

    <root>/<run-id>/
        run.json    paths and names the other verbs read   (kept)
        home/       COPILOT_OPERATOR_HOME                    (removed by down)
        venv/       the checkout under test + fake copilot   (removed by down)
        fake/       the fake copilot's package               (removed by down)
        repo/       a scratch git project, the registered one (removed by down)
        logs/       COPILOT_LOG_DIR, which the fake writes   (removed by down)
        artifacts/  transcript, screens, snapshots, agent logs (kept)

**Every child process gets the run's environment before it starts** (`_env`):

- ``COPILOT_OPERATOR_HOME``. The kernel captures it at import, so setting it
  after import addresses the real ``~/.operator``.
- The venv first on ``PATH``, so `operator` is the checkout under test and
  `copilot` is the fake. The supervisor resolves `copilot` once, in the
  environment `operator start` gave it, and hands the runner an absolute path.
- ``FAKE_PATH_FIRST``. A psmux pane gets the registry PATH, not the client's,
  so the fake puts the venv back in front before it runs `operator`. `menu`
  starts the TUI through `exec` for the same reason.
- ``PSMUX_NO_WARM=1``. Otherwise each new session server pre-spawns a warm
  server that keeps the session's working directory, and that handle stops
  `down` from removing the run.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent
FAKE_SOURCE = SKILL_DIR / "fake_copilot.py"

#: The console scripts a run drives. `doctor` checks each resolves to the venv.
DRIVEN_SCRIPTS = ("operator", "copilot")

#: The kernel's order, so the harness talks to the multiplexer operator uses.
MUX_CANDIDATES = ("tmux", "psmux", "pmux")

#: What a pane sets. Inside one, psmux refuses `attach` as a nested session.
PANE_VARIABLES = ("TMUX", "TMUX_PANE", "PSMUX_SESSION", "PSMUX_TARGET_SESSION")

#: Files under the home worth snapshotting. Globs, resolved against the home.
STATE_GLOBS = (
    "operator.log",
    "projects/catalog.csv",
    # The two halves of a handoff. The file is what the next session reads and
    # the marker is what the supervisor polls, so a proof that shows only one
    # of them cannot tell "handed off" from "ended without leaving anything".
    "projects/*/handoff/*.md",
    "restart/*",
    # Records carry the parent link, so a child proof needs them.
    "operators/*.json",
    "mail/*/*/*.json",
)

#: Removed by `down`. `artifacts/` and `run.json` are not on this list.
INSTANCE_DIRS = ("home", "venv", "fake", "repo", "logs")

#: Seconds `down` waits for the run's processes to exit after stopping them.
LEFTOVER_GRACE = 20

FAKE_PYPROJECT = """\
[build-system]
requires = ["setuptools>=64"]
build-backend = "setuptools.build_meta"

[project]
name = "verify-operator-fake-copilot"
version = "0"

[project.scripts]
copilot = "fake_copilot:main"

[tool.setuptools]
py-modules = ["fake_copilot"]
"""


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _load_fake():
    spec = importlib.util.spec_from_file_location("verify_operator_fake", FAKE_SOURCE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def checkout_root(start: Path) -> Path:
    """The checkout under test: this worktree, not the primary one."""
    out = subprocess.run(["git", "-C", str(start), "rev-parse", "--show-toplevel"],
                         capture_output=True, encoding="utf-8", errors="replace",
                         timeout=30)
    if out.returncode != 0 or not out.stdout.strip():
        raise SystemExit(f"not a git checkout: {start}")
    return Path(out.stdout.strip()).resolve()


def _home(run: Path) -> Path:
    return run / "home"


def _artifacts(run: Path) -> Path:
    return run / "artifacts"


def _scripts_dir(run: Path) -> Path:
    return run / "venv" / ("Scripts" if os.name == "nt" else "bin")


def _venv_exe(run: Path, name: str) -> Path:
    return _scripts_dir(run) / (f"{name}.exe" if os.name == "nt" else name)


def _same(a, b) -> bool:
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def _meta(run: Path) -> dict:
    path = run / "run.json"
    if not path.exists():
        raise SystemExit(f"no run at {run}; create one with `up` first")
    if not (run / "venv").is_dir():
        raise SystemExit(f"{run} is down, so nothing is left to drive; "
                         "start another with `up --run-id NEW`")
    return json.loads(path.read_text(encoding="utf-8"))


def _env(run: Path) -> dict:
    """The child's environment, settled before the process starts.

    The multiplexer's pane variables are dropped, so `operator` behaves as it
    does in a person's terminal even when this runs inside a pane: the menu
    `exec` opens, or an operator's own session driving this skill.
    """
    env = {k: v for k, v in os.environ.items() if k.upper() not in PANE_VARIABLES}
    scripts = str(_scripts_dir(run))
    env["PATH"] = scripts + os.pathsep + env.get("PATH", "")
    env["COPILOT_OPERATOR_HOME"] = str(_home(run))
    env["COPILOT_LOG_DIR"] = str(run / "logs")
    env["FAKE_RUN"] = str(run)
    env["FAKE_PATH_FIRST"] = scripts
    env["PSMUX_NO_WARM"] = "1"
    return env


def _record(run: Path, label: str, argv: list, proc) -> None:
    """Append one command to the transcript."""
    art = _artifacts(run)
    art.mkdir(parents=True, exist_ok=True)
    body = (f"\n## {label}  ({_utcnow()})\n\n"
            f"```\n$ {' '.join(map(str, argv))}\nexit {proc.returncode}\n```\n\n"
            f"stdout:\n```\n{(proc.stdout or '').rstrip()}\n```\n")
    if (proc.stderr or "").strip():
        body += f"\nstderr:\n```\n{proc.stderr.rstrip()}\n```\n"
    with open(art / "transcript.md", "a", encoding="utf-8") as fh:
        fh.write(body)


def _note(run: Path, text: str) -> None:
    art = _artifacts(run)
    art.mkdir(parents=True, exist_ok=True)
    with open(art / "transcript.md", "a", encoding="utf-8") as fh:
        fh.write(f"\n## {text}  ({_utcnow()})\n")


def _invoke(run: Path, label: str, argv: list, cwd: Path) -> int:
    # An empty pipe, so a verb that would ask a question takes its no-terminal
    # path. Not DEVNULL: on Windows NUL is a character device, isatty() says
    # True, and `delete` without --yes prompted and read EOF as No.
    proc = subprocess.run([str(a) for a in argv], cwd=str(cwd), env=_env(run),
                          capture_output=True, encoding="utf-8", errors="replace",
                          timeout=300, input="")
    _record(run, label, argv, proc)
    sys.stdout.write(proc.stdout or "")
    sys.stderr.write(proc.stderr or "")
    return proc.returncode


# ── the multiplexer ──────────────────────────────────────────────────────


def _mux() -> str | None:
    chosen = os.environ.get("COPILOT_OPERATOR_MUX")
    if chosen:
        return shutil.which(chosen) or chosen
    for candidate in MUX_CANDIDATES:
        found = shutil.which(candidate)
        if found:
            return found
    return None


def _mux_run(args: list, env: dict | None = None, cwd: str | None = None):
    binary = _mux()
    if binary is None:
        raise SystemExit("no tmux or psmux on PATH, so nothing here can open a session")
    env = dict(env or os.environ)
    env["PSMUX_NO_WARM"] = "1"
    # A neutral directory: a psmux server keeps the directory it started in.
    return subprocess.run([binary, *args], capture_output=True, encoding="utf-8",
                          errors="replace", timeout=60, env=env,
                          cwd=cwd or tempfile.gettempdir(), stdin=subprocess.DEVNULL)


def _has_session(session: str) -> bool:
    return _mux_run(["has-session", "-t", session]).returncode == 0


def _records(run: Path) -> list[dict]:
    found = []
    for path in sorted((_home(run) / "operators").glob("*.json")):
        try:
            found.append(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
    return found


def _session(run: Path, target: str) -> str:
    """`menu`, or an operator's name or id, as a multiplexer session name."""
    session = _find_session(run, target)
    if session is None:
        raise SystemExit(f"no operator named {target!r} in this run, and it is not `menu`")
    return session


def _find_session(run: Path, target: str) -> str | None:
    meta = _meta(run)
    if target == "menu":
        return meta["menu_session"]
    for record in _records(run):
        if target in (record.get("id"), record.get("name")):
            return record["id"]
    return None


def _capture(session: str) -> str | None:
    proc = _mux_run(["capture-pane", "-p", "-t", session])
    return proc.stdout if proc.returncode == 0 else None


def _read_if_there(path: Path) -> str | None:
    """A file's text, or None while it is a directory, gone, or still locked."""
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


# ── processes the run started ────────────────────────────────────────────


def _processes() -> list[dict]:
    """Every process as ``{pid, ppid, text}``, where text is exe plus command line."""
    if os.name == "nt":
        script = ("Get-CimInstance Win32_Process | Select-Object ProcessId,"
                  "ParentProcessId,ExecutablePath,CommandLine | ConvertTo-Json -Compress")
        proc = subprocess.run(["powershell", "-NoProfile", "-NonInteractive",
                               "-Command", script], capture_output=True,
                              encoding="utf-8", errors="replace", timeout=60)
        rows = json.loads(proc.stdout or "[]")
        rows = rows if isinstance(rows, list) else [rows]
        return [{"pid": r["ProcessId"], "ppid": r["ParentProcessId"],
                 "text": f"{r.get('ExecutablePath') or ''} {r.get('CommandLine') or ''}"}
                for r in rows]
    proc = subprocess.run(["ps", "-eo", "pid=,ppid=,args="], capture_output=True,
                          encoding="utf-8", errors="replace", timeout=60)
    found = []
    for line in proc.stdout.splitlines():
        parts = line.split(None, 2)
        if len(parts) >= 2:
            found.append({"pid": int(parts[0]), "ppid": int(parts[1]),
                          "text": parts[2] if len(parts) > 2 else ""})
    return found


def _ours(processes: list[dict], run: Path, me: int) -> list[dict]:
    """Processes that name the run directory, less this one and its ancestors.

    A name counts only as a whole path, so run `demo` never claims `demo2`, and
    only outside `artifacts`, which a person may have open to read the proof.
    The shell that ran this command names the run too, and is an ancestor.
    """
    parent = {p["pid"]: p["ppid"] for p in processes}
    spared, pid = set(), me
    while pid and pid not in spared:
        spared.add(pid)
        pid = parent.get(pid)
    names = re.compile(re.escape(os.path.normcase(str(run)))
                       + r"(?![^\\/\"'\s])(?![\\/]artifacts(?:[\\/\"'\s]|$))")
    return [p for p in processes
            if p["pid"] not in spared and names.search(os.path.normcase(p["text"]))]


def _launches_outside(run: Path) -> list[str]:
    """Runner launches of anything but the fake. A real Copilot spends credits."""
    fake = os.path.normcase(str(_venv_exe(run, "copilot")))
    bad = []
    for log in sorted((_home(run) / "restart").glob("*.runner.log")):
        for line in log.read_text(encoding="utf-8", errors="replace").splitlines():
            _, sep, argv = line.partition("launching: ")
            if sep and not os.path.normcase(argv).startswith(fake):
                bad.append(f"{log.name}: {line.strip()}")
    return bad


def _rmtree(path: Path) -> str | None:
    """Remove a tree, including git's read-only objects. Returns the failure."""
    def retry(func, target, _):
        os.chmod(target, stat.S_IWRITE)
        func(target)

    try:
        if sys.version_info >= (3, 12):
            shutil.rmtree(path, onexc=retry)
        else:
            shutil.rmtree(path, onerror=retry)
    except OSError as exc:
        return f"{path}: {exc}"
    return None


# ── verbs ────────────────────────────────────────────────────────────────


def _git(*args: str) -> None:
    subprocess.run(["git", *args], check=True, capture_output=True, timeout=60)


def cmd_up(args) -> int:
    root = Path(args.root).expanduser() if args.root else Path.cwd() / ".verify-operator"
    run_id = args.run_id or (datetime.now().strftime("%Y%m%d-%H%M%S-")
                             + uuid.uuid4().hex[:6])
    run = (root / run_id).resolve()
    if (run / "run.json").exists():
        raise SystemExit(f"{run} already holds a run. Pick another --run-id.")
    checkout = (Path(args.checkout).resolve() if args.checkout
                else checkout_root(Path.cwd()))
    guid = str(uuid.uuid4())
    started = time.monotonic()

    home, repo = _home(run), run / "repo"
    (home / "projects" / guid).mkdir(parents=True, exist_ok=True)
    for sub in ("scripts", "agents", "screens"):
        (_artifacts(run) / sub).mkdir(parents=True, exist_ok=True)
    (run / "logs").mkdir(parents=True, exist_ok=True)

    # A project of its own, so `--dir` worktrees and handoffs never land in the
    # checkout under test.
    _git("init", "-q", str(repo))
    _git("-C", str(repo), "-c", "user.name=verify-operator",
         "-c", "user.email=verify-operator@localhost",
         "commit", "-q", "--allow-empty", "-m", "verify-operator scratch project")
    # The path column is quoted because a path may contain a comma.
    (home / "projects" / "catalog.csv").write_text(f'"{repo}",{guid}\n',
                                                  encoding="utf-8")

    fake = run / "fake"
    fake.mkdir(parents=True, exist_ok=True)
    shutil.copy2(FAKE_SOURCE, fake / "fake_copilot.py")
    (fake / "pyproject.toml").write_text(FAKE_PYPROJECT, encoding="utf-8")

    subprocess.run([sys.executable, "-m", "venv", str(run / "venv")], check=True,
                   timeout=300)
    pip = subprocess.run(
        [str(_venv_exe(run, "python")), "-m", "pip", "install", "-q",
         "--disable-pip-version-check", "-e", str(checkout), "-e", str(fake)],
        capture_output=True, encoding="utf-8", errors="replace", timeout=600)
    if pip.returncode != 0:
        print(pip.stdout + pip.stderr, file=sys.stderr)
        raise SystemExit(f"pip could not install {checkout} into {run / 'venv'}")

    menu = "vo-" + "".join(c if c.isalnum() or c in "-_" else "-" for c in run_id)
    (run / "run.json").write_text(json.dumps({
        "created": _utcnow(), "checkout": str(checkout), "repo": str(repo),
        "guid": guid, "home": str(home), "artifacts": str(_artifacts(run)),
        "menu_session": menu,
    }, indent=2), encoding="utf-8")

    print(f"run        {run}")
    print(f"checkout   {checkout}")
    print(f"project    {repo}  ->  {guid}")
    print(f"artifacts  {_artifacts(run)}")
    print(f"ready in {time.monotonic() - started:.0f}s. Next: doctor --run {run}")
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
    env = _env(run)

    for name in DRIVEN_SCRIPTS:
        found = shutil.which(name, path=env["PATH"])
        check(f"{name} resolves to the run's venv",
              bool(found) and _same(found, _venv_exe(run, name)), found or "missing")

    fake = subprocess.run([str(_venv_exe(run, "copilot")), "--version"], env=env,
                          capture_output=True, encoding="utf-8", errors="replace",
                          timeout=60) if _venv_exe(run, "copilot").exists() else None
    check("copilot is the fake", bool(fake) and "verify-operator" in fake.stdout,
          (fake.stdout.strip() if fake else "not installed"))

    # The venv must import the checkout under test, or the run proves something
    # about whatever else is installed.
    probe = ("import json, operator_kernel; from operator_kernel import version; "
             "print(json.dumps([operator_kernel.__file__, version.__version__]))")
    python = _venv_exe(run, "python")
    out = subprocess.run([str(python), "-c", probe], env=env, capture_output=True,
                         encoding="utf-8", errors="replace",
                         timeout=60) if python.exists() else None
    try:
        kernel, kernel_version = json.loads(out.stdout)
        check("the venv imports the checkout under test",
              _same(Path(kernel).parent.parent, meta["checkout"]), kernel)
        check("kernel version readable", bool(kernel_version), kernel_version)
    except (AttributeError, TypeError, ValueError):
        check("the venv imports operator_kernel", False,
              (out.stderr.strip() if out else "no venv python"))

    home = Path(meta["home"])
    check("isolated home exists", home.is_dir(), str(home))
    check("home is not the real ~/.operator",
          not _same(home, Path.home() / ".operator"))
    catalog = home / "projects" / "catalog.csv"
    check("catalog registers the scratch project",
          catalog.exists() and meta["guid"] in catalog.read_text(encoding="utf-8"))
    check("scratch project is a git repo",
          (Path(meta["repo"]) / ".git").is_dir(), meta["repo"])
    if args.no_mux:
        print("  SKIP  multiplexer (--no-mux): start, stop, menu and mail are not drivable")
    else:
        check("tmux or psmux on PATH", _mux() is not None, _mux() or "missing")

    print("doctor:", "healthy" if ok else "NOT healthy")
    return 0 if ok else 1


def cmd_operator(args) -> int:
    """Run `operator` as a person at a shell in the scratch project would."""
    run = Path(args.run).expanduser().resolve()
    meta = _meta(run)
    cwd = Path(args.cwd).expanduser().resolve() if args.cwd else Path(meta["repo"])
    argv = [_venv_exe(run, "operator"), *args.rest]
    return _invoke(run, args.label or "operator " + " ".join(args.rest), argv, cwd)


def cmd_exec(args) -> int:
    """Run `operator` in this console with the run's environment. `menu` uses it.

    With `--hold` the console stays open after `operator` exits, showing its
    exit code, so the last screen of a menu that quit or attached can be read.
    """
    run = Path(args.run).expanduser().resolve()
    _meta(run)
    code = subprocess.call([str(_venv_exe(run, "operator")), *args.rest], env=_env(run))
    if not args.hold:
        return code
    _note(run, f"menu: operator exited {code}")
    print(f"\n[operator exited {code}]", flush=True)
    while True:
        time.sleep(3600)


def cmd_agent(args) -> int:
    """Write the steps the fake runs when it starts as operator NAME."""
    run = Path(args.run).expanduser().resolve()
    _meta(run)
    try:
        steps = [json.loads(step) for step in args.steps]
        _load_fake().validate_steps(steps)
    except ValueError as exc:
        print(f"agent: {exc}", file=sys.stderr)
        return 2
    suffix = f".s{args.session}" if args.session else ""
    path = _artifacts(run) / "scripts" / f"{args.name}{suffix}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(steps, indent=2), encoding="utf-8")
    print(f"wrote {path}")
    return 0


def cmd_menu(args) -> int:
    """Open `operator`'s menu in a session this run owns."""
    run = Path(args.run).expanduser().resolve()
    meta = _meta(run)
    session = meta["menu_session"]
    if _has_session(session):
        _mux_run(["kill-session", "-t", session])
        print(f"menu: closed the old {session} first")
    pane = [sys.executable, str(Path(__file__).resolve()), "exec", "--hold",
            "--run", str(run)]
    proc = _mux_run(["new-session", "-d", "-s", session, "-x", "160", "-y", "50",
                     "-c", meta["repo"], "--", *pane], env=_env(run))
    _record(run, f"menu: open {session}", ["new-session", session, *pane], proc)
    if proc.returncode != 0 or not _has_session(session):
        print(f"menu: could not open {session}: {proc.stderr.strip()}", file=sys.stderr)
        return 1
    print(f"menu open in session {session}. Next: wait --screen menu --contains "
          f'"Start an operator"')
    return 0


def cmd_keys(args) -> int:
    """Type into a session: literal text first, then named keys one at a time."""
    run = Path(args.run).expanduser().resolve()
    session = _session(run, args.target)
    if args.text:
        _mux_run(["send-keys", "-t", session, "-l", args.text])
    for key in args.keys:
        proc = _mux_run(["send-keys", "-t", session, key])
        if proc.returncode != 0:
            print(f"keys: {key}: {proc.stderr.strip()}", file=sys.stderr)
            return 1
        time.sleep(0.2)
    _note(run, f"keys to {args.target}: text={args.text!r} keys={' '.join(args.keys)}")
    return 0


def cmd_screen(args) -> int:
    """Save what a session's pane shows to artifacts/screens/LABEL.txt."""
    run = Path(args.run).expanduser().resolve()
    text = _capture(_session(run, args.target))
    if text is None:
        print(f"screen: no session for {args.target}", file=sys.stderr)
        return 1
    path = _artifacts(run) / "screens" / f"{args.label}.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    _note(run, f"screen {args.target} saved to screens/{args.label}.txt")
    print(text.rstrip())
    print(f"saved {path}")
    return 0


def cmd_wait(args) -> int:
    """Wait for a file under the run, or for text on a screen."""
    run = Path(args.run).expanduser().resolve()
    if args.file is not None and (not args.file or Path(args.file).is_absolute()):
        raise SystemExit(f"wait: --file takes a pattern under the run, like "
                         f"artifacts/agents/NAME/starts.log, not {args.file!r}")
    deadline = time.monotonic() + args.timeout
    last = ""
    while True:
        if args.file:
            matches = sorted(run.glob(args.file))
            texts = [text for text in map(_read_if_there, matches) if text is not None]
            last = f"{len(matches)} match(es) for {args.file}"
            hit = bool(texts) and (not args.contains
                                   or any(args.contains in t for t in texts))
        else:
            session = _find_session(run, args.screen)
            last = (_capture(session) if session else None) or ""
            hit = args.contains in last
        if hit:
            print(f"wait: found after {args.timeout - (deadline - time.monotonic()):.1f}s")
            return 0
        if time.monotonic() >= deadline:
            print(f"wait: timed out after {args.timeout}s. Last seen:\n{last.rstrip()}",
                  file=sys.stderr)
            return 1
        time.sleep(0.5)


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
    """Stop and remove what the run started. Idempotent. Keeps the artifacts."""
    run = Path(args.run).expanduser().resolve()
    if not (run / "run.json").exists():
        raise SystemExit(f"no run at {run}, so down has nothing to remove. "
                         "--run takes one run directory that `up` made.")
    report, failed = [], False
    meta = json.loads((run / "run.json").read_text(encoding="utf-8"))
    records = _records(run)

    operator = _venv_exe(run, "operator")
    if operator.exists():
        for record in records:
            code = _invoke(run, f"down: stop {record['name']}",
                           [operator, "stop", record["id"]],
                           Path(meta.get("repo") or run))
            report.append(f"stopped {record['name']} ({record['id']}), exit {code}")

    if _mux():
        sessions = [r["id"] for r in records] + [meta.get("menu_session")]
        for session in filter(None, sessions):
            if _has_session(session):
                _mux_run(["kill-session", "-t", session])
                report.append(f"killed session {session}")

    leftovers = []
    deadline = time.monotonic() + LEFTOVER_GRACE
    while True:
        leftovers = _ours(_processes(), run, os.getpid())
        if not leftovers or time.monotonic() >= deadline:
            break
        time.sleep(1)
    for proc in leftovers:
        failed = True
        report.append(f"FAIL leftover process {proc['pid']} killed: {proc['text'].strip()}")
        try:
            os.kill(proc["pid"], signal.SIGTERM)
        except OSError:
            pass

    for line in _launches_outside(run):
        failed = True
        report.append(f"FAIL a copilot other than the fake was launched: {line}")

    if _home(run).exists():
        cmd_evidence(argparse.Namespace(run=str(run), label="at-down"))
    for name in INSTANCE_DIRS:
        path = run / name
        if not path.exists():
            continue
        problem = _rmtree(path)
        failed = failed or problem is not None
        report.append(f"FAIL could not remove {problem}" if problem else f"removed {path}")

    art = _artifacts(run)
    art.mkdir(parents=True, exist_ok=True)
    report.append(f"artifacts kept at {art}")
    (art / "down.txt").write_text(f"{_utcnow()}\n" + "\n".join(report) + "\n",
                                  encoding="utf-8")
    print("\n".join(report))
    print("down:", "FAILED" if failed else "clean")
    return 1 if failed else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="control_operator.py",
        description="Drive operator against a disposable instance and keep the proof.")
    sub = parser.add_subparsers(dest="command", required=True)

    def verb(name, func, help_text, needs_run=True):
        p = sub.add_parser(name, help=help_text)
        if needs_run:
            p.add_argument("--run", required=True)
        p.set_defaults(func=func)
        return p

    up = verb("up", cmd_up, "build a venv, scratch project and home", needs_run=False)
    up.add_argument("--root", help="where run directories live "
                                   "(default: ./.verify-operator)")
    up.add_argument("--run-id", help="name this run (default: timestamp+random)")
    up.add_argument("--checkout", help="the checkout to install (default: this one)")

    doctor = verb("doctor", cmd_doctor, "read-only health check of one run")
    doctor.add_argument("--no-mux", action="store_true",
                        help="skip the multiplexer check, for machines without one")

    front = verb("operator", cmd_operator, "run `operator` in the scratch project")
    front.add_argument("--label")
    front.add_argument("--cwd", help="run from here instead of the scratch project")
    front.add_argument("rest", nargs=argparse.REMAINDER)

    run_in_pane = verb("exec", cmd_exec, "run `operator` here with the run's "
                                         "environment (what `menu` runs in its pane)")
    run_in_pane.add_argument("--hold", action="store_true",
                             help="stay open after operator exits, showing its exit code")
    run_in_pane.add_argument("rest", nargs=argparse.REMAINDER)

    agent = verb("agent", cmd_agent, "write the steps the fake runs as operator NAME")
    agent.add_argument("name")
    agent.add_argument("steps", nargs="*", help='JSON objects, e.g. \'{"op": ["list"]}\'')
    agent.add_argument("--session", type=int, help="only for this session number")

    verb("menu", cmd_menu, "open the menu in a session this run owns")

    keys = verb("keys", cmd_keys, "type into the menu or an operator's pane")
    keys.add_argument("--target", default="menu", help="`menu`, or an operator name")
    keys.add_argument("--text", help="literal text, sent before the keys")
    keys.add_argument("keys", nargs="*", help="key names such as Down, Enter, Escape")

    screen = verb("screen", cmd_screen, "save a pane to artifacts/screens/LABEL.txt")
    screen.add_argument("--target", default="menu", help="`menu`, or an operator name")
    screen.add_argument("--label", required=True)

    wait = verb("wait", cmd_wait, "wait for a file under the run or text on a screen")
    what = wait.add_mutually_exclusive_group(required=True)
    what.add_argument("--file", help="a glob relative to the run directory")
    what.add_argument("--screen", help="`menu`, or an operator name")
    wait.add_argument("--contains", help="text the file or screen must hold")
    wait.add_argument("--timeout", type=float, default=30)

    evidence = verb("evidence", cmd_evidence, "snapshot home state into artifacts")
    evidence.add_argument("--label", required=True)

    verb("down", cmd_down, "stop and remove the instance, keep the artifacts")
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(sys.argv[1:] if argv is None else argv)
    rest = getattr(args, "rest", None)
    if rest and rest[0] == "--":
        args.rest = rest[1:]
    if getattr(args, "screen", None) and not args.contains:
        raise SystemExit("wait --screen needs --contains")
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
