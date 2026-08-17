"""`operator-fleet`, and one test that runs the whole thing for real.

Most of this file is ordinary command-line testing. The one that matters is
`test_a_real_extension_reaches_the_queue_through_a_real_worker`, which is the
only test in this repository that exercises the complete path: an entry point
this package ships, discovered as an `Extension`, spawned as a separate
interpreter by `extensions.Host`, answering `propose_work` over the file
protocol, vetted through `claim_text`, appended to `proposals.jsonl` by
`FleetHost`, and read back out by the command a human runs.

Every one of those pieces already had tests. All of those tests supplied their
own extension, written by the test that asked it a question -- so the thing
never verified was whether code that has to survive packaging, discovery, a
process boundary and a JSON round trip without the test author's cooperation
actually does.

The worker is given `PYTHONPATH` because in production the package is
*installed* and therefore importable; under pytest the repository root is on
`sys.path` through pytest's own configuration, which a child process does not
inherit. Setting it is how the test stands in for an install.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import extensions
import fleet_host
from operator_cli import fleet as cli
from operator_extensions import activation, worktree_janitor

REPO = Path(__file__).resolve().parent.parent


@pytest.fixture
def home(tmp_path, monkeypatch):
    where = tmp_path / "operator-home"
    where.mkdir()
    monkeypatch.setenv("COPILOT_OPERATOR_HOME", str(where))
    return where


def queued(home: Path) -> "list[dict]":
    path = fleet_host.proposals_path(home)
    if not path.exists():
        return []
    return [json.loads(line) for line in
            path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_queue(home: Path, *records: dict) -> Path:
    path = fleet_host.proposals_path(home)
    path.write_text("".join(json.dumps(r) + "\n" for r in records),
                    encoding="utf-8")
    return path


# ── proposals ───────────────────────────────────────────────────

def test_an_empty_queue_says_so_and_succeeds(home, capsys):
    assert cli.main(["--home", str(home), "proposals"]) == 0
    assert "no proposals waiting" in capsys.readouterr().out


def test_the_queue_is_printed_with_its_attribution(home, capsys):
    write_queue(home, {"ts": "2026-08-17T10:00:00Z", "event": "work_proposed",
                       "extension": "worktree-janitor", "approved": False,
                       "text": "[worktree-janitor, unverified] tidy me",
                       "withheld": []})
    assert cli.main(["--home", str(home), "proposals"]) == 0
    out = capsys.readouterr().out
    assert "worktree-janitor" in out
    assert "tidy me" in out
    assert "1 proposal(s)" in out


def test_nothing_is_removed_without_drain(home, capsys):
    write_queue(home, {"ts": "t", "extension": "x", "text": "keep me"})
    cli.main(["--home", str(home), "proposals"])
    assert "pass --drain" in capsys.readouterr().out
    assert len(queued(home)) == 1


def test_draining_archives_the_batch_and_empties_the_queue(home, capsys):
    write_queue(home,
                {"ts": "t1", "extension": "x", "text": "one"},
                {"ts": "t2", "extension": "y", "text": "two"})
    assert cli.main(["--home", str(home), "proposals", "--drain"]) == 0
    assert "archived 2 proposal(s)" in capsys.readouterr().out

    assert queued(home) == [], "the queue must be empty after a drain"
    archive = home / "proposals.handled.jsonl"
    assert len(archive.read_text(encoding="utf-8").splitlines()) == 2


def test_draining_twice_appends_rather_than_replacing(home):
    write_queue(home, {"ts": "t1", "extension": "x", "text": "one"})
    cli.main(["--home", str(home), "proposals", "--drain"])
    write_queue(home, {"ts": "t2", "extension": "y", "text": "two"})
    cli.main(["--home", str(home), "proposals", "--drain"])

    archive = home / "proposals.handled.jsonl"
    lines = archive.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2, "an archive that replaces itself loses the history"


def test_draining_an_empty_queue_creates_no_archive(home):
    assert cli.main(["--home", str(home), "proposals", "--drain"]) == 0
    assert not (home / "proposals.handled.jsonl").exists()


def test_an_unparseable_queue_line_is_reported_not_skipped(home, capsys):
    """A corrupt tail must not look like an empty queue."""
    path = fleet_host.proposals_path(home)
    path.write_text('{"ts": "t", "extension": "x", "text": "fine"}\n'
                    '{half a record\n', encoding="utf-8")
    assert cli.main(["--home", str(home), "proposals"]) == 0
    out = capsys.readouterr().out
    assert "unparseable" in out
    assert "2 proposal(s)" in out


def test_a_drain_that_cannot_take_the_queue_reports_failure(home, monkeypatch,
                                                            capsys):
    write_queue(home, {"ts": "t", "extension": "x", "text": "one"})
    monkeypatch.setattr(cli.os, "replace",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("busy")))
    assert cli.main(["--home", str(home), "proposals", "--drain"]) == 1
    assert "could not take the queue" in capsys.readouterr().err
    assert len(queued(home)) == 1, "a failed drain must not lose the batch"


# ── run ─────────────────────────────────────────────────────────

def test_run_polls_the_requested_number_of_rounds(home, monkeypatch, capsys):
    monkeypatch.setattr(extensions, "discover", lambda *a, **k: ([], []))
    assert cli.main(["--home", str(home), "run", "--rounds", "2",
                     "--interval", "0"]) == 0
    out = capsys.readouterr().out
    assert "fleet host watching" in out
    assert "stopped after 2 round(s)" in out


def test_run_stops_on_the_marker_without_being_given_a_round_count(
        home, monkeypatch, capsys):
    """The marker is how everything else in this project asks a loop to end."""
    monkeypatch.setattr(extensions, "discover", lambda *a, **k: ([], []))
    fleet_host.fleet_stop_marker(home).write_text("", encoding="utf-8")
    assert cli.main(["--home", str(home), "run", "--interval", "0"]) == 0
    assert "stopped after 0 round(s)" in capsys.readouterr().out


def test_the_parser_requires_a_subcommand():
    with pytest.raises(SystemExit):
        cli.main([])


def test_bootstrap_puts_the_flat_module_directories_on_the_path():
    cli._bootstrap()
    import sys
    assert str(REPO / "operator_kernel") in sys.path
    assert str(REPO / "operator_fleet") in sys.path


def test_the_home_flag_beats_the_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("COPILOT_OPERATOR_HOME", str(tmp_path / "from-env"))
    assert cli._home(str(tmp_path / "from-flag")) == tmp_path / "from-flag"
    assert cli._home(None) == tmp_path / "from-env"


# ── the whole path, for real ────────────────────────────────────

def test_a_real_extension_reaches_the_queue_through_a_real_worker(
        home, tmp_path, monkeypatch):
    """Discovery, spawn, reply, vetting, queue, and the command that reads it.

    Nothing here is substituted except the entry-point *registration*, which
    stands in for the install `pyproject.toml` performs. The extension is this
    package's own module, imported by a separate interpreter that has never
    heard of this test.
    """
    monkeypatch.setenv("PYTHONPATH", str(REPO))

    import subprocess
    repo = tmp_path / "project"
    repo.mkdir()

    def git(*args):
        done = subprocess.run(["git", "-C", str(repo), *args],
                              capture_output=True, text=True, timeout=30)
        assert done.returncode == 0, done.stderr
        return done

    git("init", "-b", "main")
    git("config", "user.email", "test@example.invalid")
    git("config", "user.name", "Test")
    git("config", "commit.gpgsign", "false")
    (repo / "f.txt").write_text("x\n", encoding="utf-8")
    git("add", "f.txt")
    git("commit", "-m", "base")
    git("worktree", "add", "-b", "landed", str(repo / ".worktrees" / "landed"))

    (home / activation.CONFIG_NAME).write_text(json.dumps({
        worktree_janitor.NAME: {"enabled": True, "roots": [str(repo)],
                                "integration": "main"}}), encoding="utf-8")

    host = extensions.Host(
        [extensions.Extension(worktree_janitor.NAME,
                              "operator_extensions.worktree_janitor")],
        hooks=fleet_host.FLEET_HOOKS,
        deadline=fleet_host.FLEET_DEADLINE,
        call_deadline=fleet_host.FLEET_CALL_DEADLINE)
    fleet = fleet_host.FleetHost(host, home=home)

    proposals, failures = fleet.propose()
    assert failures == [], f"the worker reported: {failures}"
    assert len(proposals) == 1

    records = queued(home)
    assert len(records) == 1
    record = records[0]
    assert record["extension"] == worktree_janitor.NAME
    assert record["approved"] is False, "INV-WORK: nothing here may approve"
    assert record["verified"] is False
    assert "landed" in record["text"]
    assert worktree_janitor.NAME in record["text"], (
        "every line must carry its attribution out of claim_text")

    # And the command a human runs can read and clear what the fleet wrote.
    assert cli.main(["--home", str(home), "proposals", "--drain"]) == 0
    assert queued(home) == []
    archived = (home / "proposals.handled.jsonl").read_text(encoding="utf-8")
    assert "landed" in archived
