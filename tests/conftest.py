"""Fixtures for the kernel tests, ported with the tests they serve.

The multiplexer guard is carried across deliberately: in the system these
came from, a test guard that ended with `and False` gave the suite access
to the developer's real multiplexer and nearly killed seven live sessions.
"""
from __future__ import annotations
import datetime
import json
import ntpath
import os
import pathlib
import sys
import time
import warnings
from contextlib import contextmanager
from pathlib import Path
import pytest
# Imported after the kernel joins sys.path (pyproject sets `pythonpath`).
# `op` is the kernel presented as one namespace, and it is used here for the
# one property this file needs: writes forward to EVERY module binding the
# name. See tests/op.py, and `_no_real_multiplexer` below for why that matters.
import mux  # noqa: E402
import op  # noqa: E402


# ── the multiplexer boundary ────────────────────────────────────
#
# `config.MUX` is a module-level `Mux()` built at import time, so any test that
# calls into the kernel without replacing it drives the DEVELOPER'S OWN
# tmux/psmux server. Measured on 2026-08-01: 30 unit tests did exactly that,
# 134 real `tmux has-session` invocations per suite run, against whatever the
# machine happened to be running at the time.
#
# Two things follow, and the second is the expensive one:
#
#   * The tests are nondeterministic. Their answers depend on a live server, on
#     process-spawn latency and on what sessions exist right now. That is what
#     made test_unexpected_exit_without_marker_is_relaunched fail in a full run
#     and pass 3/3 alone -- five real subprocess spawns per run, none of them
#     visible in the test.
#   * They are also destructive in principle. The loop's stop path calls
#     `MUX.kill_session(instance.session)`, and its session names come from the
#     instance name in the test -- so a developer with a real session named
#     `relaunch-me` or `detach-me` loses it to a unit test.
#
# The double fakes the SUBPROCESS BOUNDARY ONLY: `_run` answers the tmux verbs
# from an in-memory session table and everything above it is the real `Mux`
# code. So new_session still verifies the session exists afterwards,
# kill_session still raises when one survives, and send_keys still splits
# literal text from Enter -- a test double built by reimplementing that surface
# would agree with whatever the implementation assumed rather than with what it
# does. An unrecognised verb raises rather than returning a plausible rc: a
# double that answers a question it does not understand is the failure this
# whole file keeps re-learning.
#
# Tests that want different behaviour still override `op.MUX` themselves; this
# runs first, so their own monkeypatch wins. Real-multiplexer coverage lives in
# test_integration.py, which builds its own `Mux()` and is untouched by this.
class FakeMux(mux.Mux):
    """A `Mux` whose backend is a dict instead of a terminal multiplexer."""

    def __init__(self, sessions: tuple[str, ...] = ()):
        super().__init__(binary="fakemux")
        self.sessions: dict[str, dict] = {
            name: {"cwd": "", "argv": [], "remain_on_exit": False, "dead": False}
            for name in sessions
        }
        self.keys: list[tuple[str, str]] = []

    @property
    def binary(self) -> str:
        return "fakemux"

    def _run(self, *args: str, capture: bool = True) -> tuple[str, str, int]:
        verb = args[0] if args else ""
        if verb == "-V":
            return "fakemux 0.0", "", 0
        if verb == "has-session":
            return "", "", 0 if args[2] in self.sessions else 1
        if verb == "list-sessions":
            return "\n".join(self.sessions), "", 0
        if verb == "new-session":
            name = args[3]
            self.sessions[name] = {
                "cwd": args[5], "argv": list(args[7:]),
                "remain_on_exit": False, "dead": False,
            }
            return "", "", 0
        if verb == "kill-session":
            name = args[2]
            if name not in self.sessions:
                return "", f"can't find session: {name}", 1
            del self.sessions[name]
            return "", "", 0
        if verb == "set-option":
            name = args[2]
            if name not in self.sessions:
                return "", f"can't find session: {name}", 1
            self.sessions[name]["remain_on_exit"] = args[4] == "on"
            return "", "", 0
        if verb == "send-keys":
            name = args[2]
            if name not in self.sessions:
                return "", f"can't find session: {name}", 1
            # Three shapes reach here and only one of them is one keystroke:
            # ("-l", text) from the literal path, (text, "Enter") from the
            # non-literal path with enter=True, and ("Enter",) from the literal
            # path's separate submit. Recording args[-1] would drop `text` from
            # the middle shape and file the send as if only Enter were typed --
            # a double answering a narrower question than the caller asked.
            payload = list(args[3:])
            if payload[:1] == ["-l"]:
                # -l takes exactly one argument and refuses a trailing key name.
                payload = payload[1:2]
            for keystroke in payload:
                self.keys.append((name, keystroke))
            return "", "", 0
        if verb == "display-message":
            name = args[2]
            session = self.sessions.get(name)
            if session is None:
                return "", f"can't find session: {name}", 1
            fmt = args[4]
            if fmt == "#{pane_dead}":
                return "1" if session["dead"] else "0", "", 0
            if fmt == "#{pane_pid}":
                return "0", "", 0
            if fmt == "#{pane_current_path}":
                return session["cwd"], "", 0
            raise AssertionError(f"FakeMux does not model display format {fmt!r}")
        if verb == "attach":
            return "", "", 0 if args[2] in self.sessions else 1
        raise AssertionError(
            f"FakeMux does not model the {verb!r} verb (args: {args!r}). "
            "Teach it the verb rather than letting a test reach the real "
            "multiplexer -- see the comment above FakeMux."
        )


_MUX_BINARIES = frozenset({"tmux", "psmux", "pmux"})


def _is_a_multiplexer_spawn(cmd) -> bool:
    """True when ``cmd`` would start a real terminal multiplexer client.

    This ended in ``and False`` for the whole life of the branch that
    introduced it -- a debug stub committed by a session that said so in its
    own commit message ("committed as recovered, before verification") and was
    killed before the verification arrived. A predicate pinned to ``False``
    does not weaken the guard, it deletes it: ``guarded_run`` then delegates
    every argv, including the ones this file exists to refuse.

    What that costs is not hypothetical and not confined to the suite's
    accuracy. ``test_the_refusal_names_the_test_and_the_argv`` runs
    ``Mux(binary="tmux")._run("kill-server")`` in the expectation of being
    stopped here. Unstopped, it is a real ``tmux kill-server``: measured on
    this machine at the moment the branch was reviewed, seven live sessions --
    six peer agents and the reviewing session itself. The test asserting that
    the guard prevents destruction becomes the thing that destroys.

    So the failure mode ran both ways at once. The three positive controls in
    test_mux_isolation.py went red, which is the loud half; the quiet half is
    that every *other* test in the suite was free to reach the real server
    again, which is the exact condition a8575d7 and 20126d6 were written to
    end. Keep this a bare membership test.

    The name is extracted with ``ntpath.basename`` rather than
    ``os.path.basename``, because ``os.path`` is the *running* platform's path
    syntax and this guard is asked about argv that name the other one. On
    POSIX, ``os.path.basename(r"C:\\tools\\tmux.exe")`` is the whole string --
    backslash is an ordinary filename character there -- so the membership test
    saw ``c:\\tools\\tmux`` and the guard delegated a tmux invocation it exists
    to refuse. That is how the parametrised case for a full Windows path passed
    on the Windows legs and spawned on the four POSIX ones, which no amount of
    local Windows testing could show.

    ``ntpath`` is the right tool rather than splitting on both separators by
    hand, and the difference is not cosmetic: the hand-rolled version read
    ``C:tmux.exe`` -- a drive-*relative* path, which Windows accepts and
    resolves against the current directory on that drive -- as the name
    ``c:tmux``, missed, and delegated. That spelling is one ``os.path.basename``
    already handled, so the hand-rolled fix would have closed the POSIX hole by
    opening a Windows one. ``ntpath`` is pure syntax with no filesystem or
    platform dependence, and it understands drive prefixes, UNC paths and both
    separators, so it is the union of the two syntaxes rather than a guess at
    it. Over-refusing inside the test suite costs a loud error; under-refusing
    costs a real server.
    """
    head = cmd[0] if isinstance(cmd, (list, tuple)) and cmd else cmd
    name = ntpath.basename(str(head)).lower()
    if name.endswith(".exe"):
        name = name[:-4]
    return name in _MUX_BINARIES


@pytest.fixture(autouse=True)
def _no_real_multiplexer(request: pytest.FixtureRequest):
    """Point the kernel's `MUX` at an empty in-memory multiplexer, and make any
    *other* route to a real one raise.

    An empty one, because that is what the leaking tests were already getting
    by accident: no session of theirs exists on the real server, so every
    `has_session` came back False. The behaviour they assert is unchanged; only
    its dependence on the machine goes away.

    **The substitution is written through `op`, and that is the whole of why it
    works.** It was carried into this repository as
    `copilot_operator.MUX = FakeMux()` -- the attribute on the 9,120-line module
    this kernel was extracted *from*, which is not a module the kernel reads and
    is not even in this repository. So for the whole life of the extraction the
    substitution half of this guard was INERT: measured 2026-08-15, `config.MUX`
    and `launch.MUX` were both a real `Mux` inside a running test. Only the
    spawn poison below was holding the line, which is why the leak showed up as
    loud AssertionErrors in `tests/pending/` rather than as tests quietly
    driving this machine's server. A guard that has been rewired past its target
    still reads exactly like a guard; that is the failure this file keeps
    re-learning, and this is its third instance.

    `op.MUX = ...` and not `config.MUX = ...`, because seven kernel modules do
    `from config import MUX` and hold their own reference. Writing only to
    `config` would leave `launch`, `supervisor`, `session_state`, `snapshot`,
    `instance` and `supervisor_control` pointed at the real server -- a
    substitution that looks applied, in the file whose subject is substitutions
    that look applied. `op.__setattr__` forwards to every module binding the
    name; `test_mux_isolation.py` asserts that the set it reaches is complete.

    Substituting is not on its own enough either, and the gap is the kind that
    stays quiet. It closes the route the 30 leaking tests took; it does nothing
    about a test that builds its own `Mux()` -- which is exactly what
    test_integration.py does, deliberately, so the pattern is already in the
    file a newcomer copies from. A substitution cannot report what it did not
    intercept, so the second half poisons the spawn itself: any attempt to start
    a tmux/psmux/pmux client fails, loudly, naming the argv. Every other
    subprocess is delegated untouched -- the suite really does run Python child
    processes and must keep being able to.

    Tests that mean to drive a real server mark themselves `real_multiplexer`
    and are exempted from both halves.

    It saves and restores by hand rather than taking `monkeypatch`, and that is
    not a style choice. An autouse fixture that REQUESTS `monkeypatch` pulls
    monkeypatch's lifetime up to its own, so monkeypatch is finalised earlier
    than it used to be -- and `_no_stray_artifacts` above then runs its
    end-of-test scan with the test's patches STILL APPLIED. Doing that here
    turned all 26 tests in test_artifact_guard.py into teardown errors, because
    they patch `_GUARDED_DIRS` to their own tmp_path and the guard duly found
    their fixtures there. Depending on no ordinary fixture keeps this one first
    to set up and last to tear down, which is also the only order in which a
    test's own `monkeypatch.setattr(op, "MUX", ...)` is undone before this
    restores. `request` is exempt from that hazard: it is not finalised into
    the same stack.
    """
    if "real_multiplexer" in request.keywords:
        yield
        return

    real_mux = op.MUX
    real_run = mux.subprocess.run

    def guarded_run(cmd, *args, **kwargs):
        if _is_a_multiplexer_spawn(cmd):
            raise AssertionError(
                f"{request.node.nodeid} tried to start a real terminal "
                f"multiplexer: {cmd!r}. Unit tests must not drive this "
                f"machine's tmux/psmux server -- their answers then depend on "
                f"what happens to be running. Use conftest's FakeMux, or mark "
                f"the test `real_multiplexer` if it genuinely needs one."
            )
        return real_run(cmd, *args, **kwargs)

    op.MUX = FakeMux()
    mux.subprocess.run = guarded_run
    try:
        yield
    finally:
        mux.subprocess.run = real_run
        op.MUX = real_mux


@pytest.fixture
def state_dir(tmp_path: Path) -> Path:
    d = tmp_path / "restart"
    d.mkdir(parents=True)
    return d


@contextmanager
def denied(monkeypatch, *paths, limit: int | None = None, counter=None):
    """Make every stat of ``paths`` raise EACCES, as a revoked directory does.

    Three call sites are patched, not one. ``Path.exists()`` reaches the
    filesystem through ``os.stat`` -- and on 3.10 through a private pathlib
    accessor that copies ``os.stat`` at import time -- while the tri-state
    probes in the kernel call ``os.lstat`` directly. Deny only
    ``os.lstat`` and code that still uses ``exists()`` sails through, so the
    test grades nothing; deny only ``os.stat`` and the probes never see the
    failure they exist to handle; miss the accessor and the whole thing is
    vacuous on 3.10.

    ``limit`` denies only the first N probes, which is how a transient failure
    (a scanner holding a file open on Windows) actually behaves: the next poll
    succeeds.
    """
    targets = {str(Path(p)) for p in paths}
    seen = counter if counter is not None else {"n": 0}
    real_stat, real_lstat = os.stat, os.lstat

    def guard(real):
        def probe(path, *args, **kwargs):
            try:
                key = str(Path(path))
            except TypeError:
                key = None
            if key in targets and (limit is None or seen["n"] < limit):
                seen["n"] += 1
                raise PermissionError(13, "Permission denied")
            return real(path, *args, **kwargs)
        return probe

    monkeypatch.setattr(os, "stat", guard(real_stat))
    monkeypatch.setattr(os, "lstat", guard(real_lstat))
    accessor = getattr(pathlib, "_NormalAccessor", None)
    saved = {}
    if accessor is not None:
        for name, real in (("stat", real_stat), ("lstat", real_lstat)):
            if hasattr(accessor, name):
                saved[name] = getattr(accessor, name)
                setattr(accessor, name, staticmethod(guard(real)))
    try:
        yield seen
    finally:
        for name, original in saved.items():
            setattr(accessor, name, original)
        monkeypatch.setattr(os, "stat", real_stat)
        monkeypatch.setattr(os, "lstat", real_lstat)


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "metrics.db"


def make_log(
    path: Path,
    *,
    premium_calls=((("claude-opus-4.6", 3.0),) * 1),
    lines_added: int = 10,
    lines_removed: int = 2,
    cwd: str = "/home/dev/project",
    extra_text: str = "",
) -> Path:
    """Write a synthetic Copilot process log containing a shutdown event."""
    header = (
        '2026-07-27T10:00:00.000Z [info] starting\n'
        f'2026-07-27T10:00:00.100Z [info] {{"cwd": "{cwd}"}}\n'
    )
    usage_blocks = []
    for model, cost in premium_calls:
        usage_blocks.append(
            '2026-07-27T10:01:00.000Z [telemetry] {\n'
            '  "kind": "assistant_usage",\n'
            f'  "model": "{model}",\n'
            f'  "cost": {cost}\n'
            '}\n'
        )
    shutdown = (
        '2026-07-27T10:30:00.000Z [telemetry] {\n'
        '  "kind": "session_shutdown",\n'
        '  "properties": {\n'
        '    "model_claude-opus-4.6_input_tokens": "1500000",\n'
        '    "model_claude-opus-4.6_output_tokens": "24000",\n'
        '    "model_claude-opus-4.6_cache_read_tokens": "900",\n'
        '    "model_claude-opus-4.6_request_count": "7"\n'
        '  },\n'
        '  "metrics": {\n'
        '    "total_premium_requests": 1,\n'
        '    "total_api_duration_ms": 120000,\n'
        '    "session_duration_ms": 1800000,\n'
        f'    "lines_added": {lines_added},\n'
        f'    "lines_removed": {lines_removed}\n'
        '  }\n'
        '}\n'
    )
    footer = '2026-07-27T10:30:05.000Z [info] done\n'
    path.write_text(
        header + extra_text + "".join(usage_blocks) + shutdown + footer,
        encoding="utf-8",
    )
    return path


@pytest.fixture
def launch_spec(tmp_path: Path, state_dir: Path, db_path: Path):
    def _make(argv, session_num=1, log_dir=None):
        spec = {
            "instance": "testinst",
            "argv": list(argv),
            "cwd": str(tmp_path),
            "session_num": session_num,
            "state_dir": str(state_dir),
            "metrics_db": str(db_path),
            "copilot_log_dir": str(log_dir or (tmp_path / "logs")),
        }
        path = tmp_path / "spec.json"
        path.write_text(json.dumps(spec), encoding="utf-8")
        return path

    return _make
