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
import os
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


def test_the_queue_is_taken_before_it_is_read(home):
    """Anything the host appends after the claim belongs to the next drain.

    The first version read the file and *then* renamed it, so a proposal
    written in between was archived without ever being printed -- a human
    filing away work they were never shown. Two reviewers found it
    independently.
    """
    write_queue(home, {"ts": "t1", "extension": "x", "text": "before"})
    path = fleet_host.proposals_path(home)
    mine = path.with_name("proposals.draining.test.jsonl")

    batches = cli._claim(path, mine)
    assert not path.exists(), "the queue must be moved aside by the claim"

    # The fleet host carries on while the archive is being written.
    write_queue(home, {"ts": "t2", "extension": "y", "text": "after"})

    taken = cli._read(batches[-1])
    assert [r["text"] for r in taken] == ["before"]
    assert [r["text"] for r in cli._read(path)] == ["after"]


def test_a_batch_abandoned_by_a_crashed_drain_is_adopted_by_the_next(
        home, capsys):
    """The live queue has already been reset, so nothing else would find it."""
    path = fleet_host.proposals_path(home)
    orphan = path.with_name("proposals.draining.99999.jsonl")
    orphan.write_text(json.dumps(
        {"ts": "t0", "extension": "x", "text": "lost in a crash"}) + "\n",
        encoding="utf-8")
    write_queue(home, {"ts": "t1", "extension": "y", "text": "current"})

    assert cli.main(["--home", str(home), "proposals", "--drain"]) == 0
    out = capsys.readouterr().out
    assert "recovered an abandoned batch" in out
    assert "lost in a crash" in out, "the recovered batch must also be shown"

    assert not orphan.exists()
    archive = (home / "proposals.handled.jsonl").read_text(encoding="utf-8")
    assert "lost in a crash" in archive and "current" in archive
    assert cli.main(["--home", str(home), "proposals"]) == 0


def test_an_orphan_is_recovered_even_when_the_queue_is_empty(home):
    path = fleet_host.proposals_path(home)
    orphan = path.with_name("proposals.draining.88888.jsonl")
    orphan.write_text(json.dumps(
        {"ts": "t0", "extension": "x", "text": "only orphan"}) + "\n",
        encoding="utf-8")

    assert cli.main(["--home", str(home), "proposals", "--drain"]) == 0
    assert not orphan.exists()
    assert "only orphan" in (
        home / "proposals.handled.jsonl").read_text(encoding="utf-8")


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


def test_bootstrap_puts_the_flat_module_directories_on_the_path(monkeypatch):
    """Falsifiable only if the directories are absent when it is called.

    pytest already puts both on `sys.path` through `pythonpath`, so the first
    version of this test passed whether or not `_bootstrap` did anything at
    all -- a reviewer pointed out it would survive the function being replaced
    by `pass`. They are removed first now.
    """
    import sys as _sys
    kernel = str(REPO / "operator_kernel")
    fleet = str(REPO / "operator_fleet")
    monkeypatch.setattr(
        _sys, "path", [p for p in _sys.path if p not in (kernel, fleet)])
    assert kernel not in _sys.path and fleet not in _sys.path

    cli._bootstrap()
    assert kernel in _sys.path, "the kernel directory was not restored"
    assert fleet in _sys.path, "the fleet directory was not restored"


def test_the_home_flag_beats_the_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("COPILOT_OPERATOR_HOME", str(tmp_path / "from-env"))
    assert cli._home(str(tmp_path / "from-flag")) == tmp_path / "from-flag"
    assert cli._home(None) == tmp_path / "from-env"


def test_the_home_flag_is_exported_so_spawned_workers_agree(home, tmp_path,
                                                            monkeypatch):
    """Extensions run in workers that resolve the operator home themselves.

    `--home` used to move the ledger and the queue and nothing else, so a
    relocated fleet read its activation config and wrote its extension state
    under the real `~/.operator`.
    """
    elsewhere = tmp_path / "relocated"
    elsewhere.mkdir()
    monkeypatch.setenv("COPILOT_OPERATOR_HOME", str(home))

    assert cli.main(["--home", str(elsewhere), "proposals"]) == 0
    assert os.environ["COPILOT_OPERATOR_HOME"] == str(elsewhere), (
        "a worker spawned after this would read the wrong operator home")


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
