"""Tests for the in-pane session supervisor."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

import runner
from conftest import make_log


# ── log attribution ─────────────────────────────────────────────
def test_find_log_matches_exact_pid(tmp_path):
    logs = tmp_path / "logs"
    logs.mkdir()
    mine = logs / "process-1700000000000-4242.log"
    mine.write_text("x", encoding="utf-8")
    (logs / "process-1700000000000-9999.log").write_text("x", encoding="utf-8")
    assert runner._find_log(logs, 4242, 1700000000000) == mine


def test_find_log_never_falls_back_to_newest(tmp_path):
    """The bash version grabbed the newest log on a PID miss, which let one
    instance record another's usage. A miss must return None."""
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "process-1700000000000-1111.log").write_text("x", encoding="utf-8")
    assert runner._find_log(logs, 4242, 1700000000000) is None


def test_find_log_ignores_older_launches(tmp_path):
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "process-1000-4242.log").write_text("old", encoding="utf-8")
    current = logs / "process-1700000000000-4242.log"
    current.write_text("new", encoding="utf-8")
    assert runner._find_log(logs, 4242, 1699999999999) == current


def test_find_log_ignores_logs_far_after_launch(tmp_path):
    """PID reuse: a much later Copilot run that happened to get the same PID
    must not be attributed to this session."""
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "process-1800000000000-4242.log").write_text("much later", encoding="utf-8")
    assert runner._find_log(logs, {4242}, 1700000000000) is None


def test_find_log_prefers_the_launch_closest_in_time(tmp_path):
    logs = tmp_path / "logs"
    logs.mkdir()
    near = logs / "process-1700000001000-4242.log"
    near.write_text("near", encoding="utf-8")
    (logs / "process-1700000500000-4242.log").write_text("far", encoding="utf-8")
    assert runner._find_log(logs, {4242}, 1700000000000) == near


def test_find_log_handles_missing_directory(tmp_path):
    assert runner._find_log(tmp_path / "nope", 1, 0) is None


# ── session id extraction ───────────────────────────────────────
def test_extract_session_id_from_json_field(tmp_path):
    log = tmp_path / "a.log"
    log.write_text(
        'noise\n{"session_id": "3f2a9c1e-1111-2222-3333-444455556666"}\n',
        encoding="utf-8",
    )
    assert runner._extract_session_id(log) == \
        "3f2a9c1e-1111-2222-3333-444455556666"


def test_extract_session_id_from_workspace_line(tmp_path):
    log = tmp_path / "b.log"
    log.write_text(
        "Workspace initialized: aaaabbbb-cccc-dddd-eeee-ffff00001111\n",
        encoding="utf-8",
    )
    assert runner._extract_session_id(log) == \
        "aaaabbbb-cccc-dddd-eeee-ffff00001111"


def test_extract_session_id_absent(tmp_path):
    log = tmp_path / "c.log"
    log.write_text("nothing to see\n", encoding="utf-8")
    assert runner._extract_session_id(log) is None


# ── end-to-end supervision ──────────────────────────────────────
def test_runner_records_pid_and_exit_code(tmp_path, state_dir, db_path, launch_spec):
    spec = launch_spec([sys.executable, "-c", "import sys; sys.exit(7)"])
    rc = runner.run(spec)
    assert rc == 7
    assert (state_dir / "testinst.exit").read_text(encoding="utf-8").strip() == "7"
    # The pid file is transient and removed once the child exits.
    assert not (state_dir / "testinst.pid").exists()


def test_runner_clears_stale_exit_marker(tmp_path, state_dir, db_path, launch_spec):
    (state_dir / "testinst.exit").write_text("99", encoding="utf-8")
    spec = launch_spec([sys.executable, "-c", "pass"])
    runner.run(spec)
    assert (state_dir / "testinst.exit").read_text(encoding="utf-8").strip() == "0"


def test_runner_reports_missing_executable(tmp_path, state_dir, launch_spec):
    spec = launch_spec(["definitely-not-a-real-binary-xyz"])
    assert runner.run(spec) == 127
    assert (state_dir / "testinst.exit").read_text(encoding="utf-8").strip() == "127"












# ── the marker is published whole, never half-written ───────────
def test_the_exit_marker_is_never_observable_empty(tmp_path, monkeypatch):
    """A marker that exists but is empty is read two different ways.

    `is_copilot_running` treats presence alone as authoritative and reports
    the session over; `read_exit_code` parses an empty file as None, which
    `ending_was_observed` reads as "nobody saw this end" -- the signature of
    an externally killed pane. A supervisor polling into the window between
    `write_text`'s truncate and its write would file a clean exit as an
    unexplained kill, which is the exact misclassification this whole change
    exists to remove.

    Asserted by watching every write that lands under the marker's own name:
    with an atomic publish there is none, because the content is written to a
    temporary and renamed into place.
    """
    marker = tmp_path / "inst.exit"
    real_write = Path.write_text
    direct_writes = []

    def spy(self, data, *args, **kwargs):
        if self == marker:
            direct_writes.append(data)
        return real_write(self, data, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", spy)
    runner._publish_exit_code(marker, 42)

    assert marker.read_text(encoding="utf-8").strip() == "42"
    assert direct_writes == [], (
        "the marker was written in place, so a reader can see it exist while "
        "it is still empty"
    )


def test_a_failed_exit_publish_leaves_no_temporary_behind(tmp_path, monkeypatch):
    """The state dir is polled by name; litter there is read by other code."""
    marker = tmp_path / "inst.exit"

    def refuse(_src, _dst):
        raise OSError("read-only")

    monkeypatch.setattr(runner.os, "replace", refuse)
    with pytest.raises(OSError):
        runner._publish_exit_code(marker, 7)

    assert not marker.exists()
    assert list(tmp_path.iterdir()) == [], (
        f"left behind {[p.name for p in tmp_path.iterdir()]}"
    )




# ── launch spec validation ──────────────────────────────────────
def _write_spec(path: Path, spec) -> Path:
    path.write_text(json.dumps(spec), encoding="utf-8")
    return path


def _valid_spec(tmp_path: Path, state_dir: Path, db_path: Path) -> dict:
    return {
        "instance": "testinst",
        "argv": [sys.executable, "-c", "pass"],
        "cwd": str(tmp_path),
        "session_num": 1,
        "state_dir": str(state_dir),
        "metrics_db": str(db_path),
        "copilot_log_dir": str(tmp_path / "logs"),
    }


def test_bad_spec_exit_code_is_distinct_from_a_session_failure():
    """The parent must be able to tell 'launched wrong' from 'session failed'.

    0, 126 and 127 already mean something else in this module.
    """
    assert runner.EXIT_BAD_SPEC not in (0, 126, 127)


def test_missing_spec_file_is_reported_not_raised(tmp_path, capsys):
    """A vanished spec used to exit with a bare FileNotFoundError traceback."""
    spec = tmp_path / "testinst.launch.json"
    assert runner.run(spec) == runner.EXIT_BAD_SPEC
    err = capsys.readouterr().err
    assert str(spec) in err


def test_unreadable_spec_still_writes_an_exit_marker(tmp_path):
    """The supervisor dying silently is the worst case: the loop polls for
    `{id}.exit` and would otherwise never learn why the pane went away. The
    spec path alone names both the state dir and the instance."""
    spec = tmp_path / "testinst.launch.json"
    runner.run(spec)
    marker = tmp_path / "testinst.exit"
    assert marker.read_text(encoding="utf-8").strip() == str(runner.EXIT_BAD_SPEC)


def test_truncated_spec_is_reported(tmp_path, capsys):
    """A spec caught mid-write by a crash is the realistic corruption."""
    spec = tmp_path / "testinst.launch.json"
    spec.write_text('{"instance": "testinst", "argv": [', encoding="utf-8")
    assert runner.run(spec) == runner.EXIT_BAD_SPEC
    assert (tmp_path / "testinst.exit").exists()
    assert "not valid JSON" in capsys.readouterr().err


@pytest.mark.parametrize("payload", [[], "a string", 12, None])
def test_spec_must_be_a_json_object(tmp_path, payload):
    spec = _write_spec(tmp_path / "testinst.launch.json", payload)
    assert runner.run(spec) == runner.EXIT_BAD_SPEC


@pytest.mark.parametrize("key", [
    "instance", "argv", "cwd", "state_dir", "copilot_log_dir",
])
def test_missing_required_key_names_the_key(tmp_path, state_dir, db_path, key, capsys):
    body = _valid_spec(tmp_path, state_dir, db_path)
    del body[key]
    spec = _write_spec(tmp_path / "testinst.launch.json", body)
    assert runner.run(spec) == runner.EXIT_BAD_SPEC
    err = capsys.readouterr().err
    assert key in err, f"diagnostic must name the offending key, got: {err}"
    assert str(spec) in err, "diagnostic must name the offending file"


def test_string_argv_is_rejected_rather_than_split_per_character(
    tmp_path, state_dir, db_path, monkeypatch
):
    """`list("--loop")` is a valid expression and a catastrophic launch: it
    would spawn a process named '-' with each remaining character as its own
    argument. Nothing may be spawned."""
    body = _valid_spec(tmp_path, state_dir, db_path)
    body["argv"] = "--loop --name a"
    spec = _write_spec(tmp_path / "testinst.launch.json", body)

    def explode(*args, **kwargs):
        raise AssertionError("a malformed spec must never spawn a process")

    monkeypatch.setattr(runner.subprocess, "Popen", explode)
    assert runner.run(spec) == runner.EXIT_BAD_SPEC


def test_empty_argv_is_rejected(tmp_path, state_dir, db_path):
    """Popen([]) raises IndexError, which the FileNotFoundError/OSError
    handlers around the spawn do not catch."""
    body = _valid_spec(tmp_path, state_dir, db_path)
    body["argv"] = []
    spec = _write_spec(tmp_path / "testinst.launch.json", body)
    assert runner.run(spec) == runner.EXIT_BAD_SPEC


@pytest.mark.parametrize("argv", [[None], ["ok", 5], [["nested"]]])
def test_argv_entries_must_be_strings(tmp_path, state_dir, db_path, argv):
    body = _valid_spec(tmp_path, state_dir, db_path)
    body["argv"] = argv
    spec = _write_spec(tmp_path / "testinst.launch.json", body)
    assert runner.run(spec) == runner.EXIT_BAD_SPEC


@pytest.mark.parametrize("value", ["", "   ", None, 5, []])
def test_string_keys_must_be_non_empty_strings(tmp_path, state_dir, db_path, value):
    body = _valid_spec(tmp_path, state_dir, db_path)
    body["instance"] = value
    spec = _write_spec(tmp_path / "testinst.launch.json", body)
    assert runner.run(spec) == runner.EXIT_BAD_SPEC


@pytest.mark.parametrize("value", ["3", 1.5, None, True])
def test_session_num_must_be_an_integer(tmp_path, state_dir, db_path, value):
    """`int(spec['session_num'])` raised ValueError/TypeError before any
    marker was written. `True` is rejected too: bool would silently become 1."""
    body = _valid_spec(tmp_path, state_dir, db_path)
    body["session_num"] = value
    spec = _write_spec(tmp_path / "testinst.launch.json", body)
    assert runner.run(spec) == runner.EXIT_BAD_SPEC


def test_marker_lands_where_the_parent_is_watching(tmp_path, state_dir, db_path):
    """The operator writes the spec to `{state_dir}/{instance}.launch.json`
    and polls `{state_dir}/{instance}.exit`, so the spec's own path is the one
    location known to be watched. A spec that already failed validation must
    not be able to redirect the report somewhere nobody looks."""
    body = _valid_spec(tmp_path, state_dir, db_path)
    del body["argv"]
    diverted = tmp_path / "diverted"
    body["state_dir"] = str(diverted)
    spec = _write_spec(state_dir / "testinst.launch.json", body)
    assert runner.run(spec) == runner.EXIT_BAD_SPEC
    assert (state_dir / "testinst.exit").read_text(encoding="utf-8").strip() \
        == str(runner.EXIT_BAD_SPEC)
    assert not diverted.exists(), "a failed spec must not steer the marker"


def test_bad_spec_is_recorded_in_the_runner_log(tmp_path, state_dir, db_path):
    body = _valid_spec(tmp_path, state_dir, db_path)
    body["argv"] = []
    spec = _write_spec(tmp_path / "testinst.launch.json", body)
    runner.run(spec)
    log = (tmp_path / "testinst.runner.log").read_text(encoding="utf-8")
    assert "invalid launch spec" in log
    assert str(spec) in log


@pytest.mark.parametrize("key", [
    "instance", "cwd", "state_dir", "copilot_log_dir",
])
def test_embedded_nul_in_a_string_key_is_rejected(
    tmp_path, state_dir, db_path, key, monkeypatch
):
    """A NUL survives JSON but makes filesystem calls and `Popen` raise
    ValueError, which is not an OSError -- so it escaped every guard and
    crashed after validation had already "passed". Found by adversarial
    review."""
    body = _valid_spec(tmp_path, state_dir, db_path)
    body[key] = f"bad\x00{key}"

    def explode(*args, **kwargs):
        raise AssertionError("a spec with a NUL must never spawn a process")

    monkeypatch.setattr(runner.subprocess, "Popen", explode)
    spec = _write_spec(tmp_path / "testinst.launch.json", body)
    assert runner.run(spec) == runner.EXIT_BAD_SPEC
    assert (tmp_path / "testinst.exit").exists()


def test_embedded_nul_in_argv_is_rejected(tmp_path, state_dir, db_path, monkeypatch):
    body = _valid_spec(tmp_path, state_dir, db_path)
    body["argv"] = ["python", "arg\x00two"]

    def explode(*args, **kwargs):
        raise AssertionError("a spec with a NUL must never spawn a process")

    monkeypatch.setattr(runner.subprocess, "Popen", explode)
    spec = _write_spec(tmp_path / "testinst.launch.json", body)
    assert runner.run(spec) == runner.EXIT_BAD_SPEC
    assert (tmp_path / "testinst.exit").exists()


def test_reporter_never_raises_even_when_it_cannot_write(tmp_path, monkeypatch, capsys):
    """`_report_bad_spec` is the last-resort reporter. If it throws, it
    restores the exact bare-traceback failure it exists to prevent."""
    spec = tmp_path / "testinst.launch.json"
    spec.write_text("{", encoding="utf-8")

    def refuse(*args, **kwargs):
        raise ValueError("embedded null character in path")

    monkeypatch.setattr(runner.Path, "mkdir", refuse)
    assert runner.run(spec) == runner.EXIT_BAD_SPEC
    assert "invalid launch spec" in capsys.readouterr().err


def test_spawn_value_error_is_reported_with_a_marker(
    tmp_path, state_dir, db_path, monkeypatch
):
    """Defense in depth: ValueError is not caught by the FileNotFoundError or
    OSError handlers around the spawn."""
    def boom(*args, **kwargs):
        raise ValueError("embedded null character")

    monkeypatch.setattr(runner.subprocess, "Popen", boom)
    spec = _write_spec(tmp_path / "testinst.launch.json",
                       _valid_spec(tmp_path, state_dir, db_path))
    assert runner.run(spec) == runner.EXIT_BAD_SPEC
    assert (state_dir / "testinst.exit").read_text(encoding="utf-8").strip() \
        == str(runner.EXIT_BAD_SPEC)


def test_bad_spec_creates_a_missing_state_dir(tmp_path, db_path):
    """The failure has to be observable even when the directory does not
    exist yet."""
    missing = tmp_path / "no-such-dir"
    spec = missing / "testinst.launch.json"
    assert runner.run(spec) == runner.EXIT_BAD_SPEC
    assert (missing / "testinst.exit").exists()


def test_main_propagates_the_bad_spec_code(tmp_path):
    """The CLI entry point is what the mux actually runs."""
    spec = tmp_path / "testinst.launch.json"
    assert runner.main([str(spec)]) == runner.EXIT_BAD_SPEC


def test_valid_spec_still_runs(tmp_path, state_dir, db_path, monkeypatch):
    """Validation must not reject what the operator actually writes."""
    monkeypatch.setattr(runner.time, "sleep", lambda s: None)
    spec = _write_spec(tmp_path / "testinst.launch.json",
                       _valid_spec(tmp_path, state_dir, db_path))
    assert runner.run(spec) == 0
    assert (state_dir / "testinst.exit").read_text(encoding="utf-8").strip() == "0"


def test_binary_spec_is_reported_not_raised(tmp_path):
    """UnicodeDecodeError is not an OSError, so reading binary garbage escaped
    the read guard and produced exactly the bare traceback this fix exists to
    remove. Found by adversarial review."""
    spec = tmp_path / "testinst.launch.json"
    spec.write_bytes(b"\xff\xfe\xff")
    assert runner.run(spec) == runner.EXIT_BAD_SPEC
    assert (tmp_path / "testinst.exit").read_text(encoding="utf-8").strip() \
        == str(runner.EXIT_BAD_SPEC)


@pytest.mark.parametrize("instance", [
    "..", ".", "../escaped", "..\\escaped", "sub/inst", "/abs", "a\x00b",
])
def test_instance_may_not_escape_the_state_directory(
    tmp_path, state_dir, db_path, instance, monkeypatch
):
    """`instance` is interpolated into `{instance}.exit` / `.pid` / `.log`, so
    a traversal value would write outside the directory the parent watches.
    Found by adversarial review."""
    body = _valid_spec(tmp_path, state_dir, db_path)
    body["instance"] = instance

    def explode(*args, **kwargs):
        raise AssertionError("an unsafe instance name must never spawn a process")

    monkeypatch.setattr(runner.subprocess, "Popen", explode)
    spec = _write_spec(tmp_path / "testinst.launch.json", body)
    assert runner.run(spec) == runner.EXIT_BAD_SPEC
    escaped = list(tmp_path.parent.glob("escaped.*"))
    assert not escaped, f"wrote outside the state dir: {escaped}"


def test_unsafe_instance_in_a_bad_spec_does_not_steer_the_marker(
    tmp_path, state_dir, db_path
):
    """The reporting path runs on a spec that failed validation, so nothing
    it says about its own identity is trusted."""
    body = _valid_spec(tmp_path, state_dir, db_path)
    body["instance"] = "..\\escaped"
    del body["argv"]
    spec = _write_spec(state_dir / "testinst.launch.json", body)
    assert runner.run(spec) == runner.EXIT_BAD_SPEC
    assert (state_dir / "testinst.exit").exists(), \
        "must fall back to the path-derived instance name"
    assert not list(state_dir.parent.glob("escaped.*"))


@pytest.mark.parametrize("instance", ["a,b", "copilot-tools", "a.b", "a-b_c"])
def test_real_instance_names_are_accepted(tmp_path, state_dir, db_path,
                                          instance, monkeypatch):
    """Guard against over-tightening: these are live instance ids on this
    machine, and `a,b` in particular is a real one."""
    monkeypatch.setattr(runner.time, "sleep", lambda s: None)
    body = _valid_spec(tmp_path, state_dir, db_path)
    body["instance"] = instance
    spec = _write_spec(tmp_path / f"{instance}.launch.json", body)
    assert runner.run(spec) == 0
    assert (state_dir / f"{instance}.exit").exists()


# ── the exit marker is the last thing the runner does ───────────
#
# In the system this kernel came from, the marker was published *before* a
# metrics capture, and the ordering was load-bearing: the capture measured 7s to
# 13.3 hours, averaging 95 minutes, and anything that killed the runner inside
# that window destroyed the exit code -- the one fact separating "the agent
# crashed on its own" from "something took the whole pane". Of 1042 recorded
# endings, 3 carried a code.
#
# The kernel has no metrics capture, so the property is now stronger and simpler
# to state: nothing follows the publish. These tests pin that, because a future
# addition after it would silently restore the window.
def test_publishing_the_exit_marker_is_the_last_thing_run_does():
    """Nothing may follow the publish, because something once did.

    The marker was written after a metrics capture that measured 7s to 13.3
    hours. Anything killing the runner inside that window destroyed the exit
    code -- the one fact separating "the agent crashed on its own" from
    "something took the whole pane". Of 1042 recorded endings, 3 carried one.
    The kernel has no capture, so the property is now simply: publish is last.
    """
    import ast, inspect

    fn = ast.parse(inspect.getsource(runner.run)).body[0]
    tail = [n for n in fn.body if not isinstance(n, ast.Return)][-1]
    assert "_publish_exit_code" in ast.dump(tail), (
        "the exit marker is no longer the last thing `run` does; something was "
        "added after it, which is the blind window returning"
    )


def _code_only(source: str) -> str:
    """Source with comments and docstrings removed.

    The comments explaining *why* metrics are absent must not themselves trip
    the check that they are absent.
    """
    import ast, io, tokenize

    out = []
    for tok in tokenize.generate_tokens(io.StringIO(source).readline):
        if tok.type == tokenize.COMMENT:
            continue
        out.append(tok)
    stripped = tokenize.untokenize(out)
    tree = ast.parse(stripped)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef)) and ast.get_docstring(node):
            node.body = node.body[1:] or [ast.Pass()]
    return ast.unparse(tree)


def test_the_runner_does_not_reach_for_metrics_at_all():
    """Metrics is the concern this kernel was extracted away from."""
    import inspect

    code = _code_only(inspect.getsource(runner))
    for name in ("operator_ingest", "ingest_file", "metrics_db"):
        assert name not in code, (
            f"the kernel runner references {name!r} in code; metrics capture "
            f"belongs on the other side of the boundary"
        )
