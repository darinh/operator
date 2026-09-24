"""The `operator-seat` command line, and the one thing it kept getting wrong.

The launch preamble tells every unattended session to write its notes with
`operator-seat remember --instance <id> --kind gotcha "..."`. That line did not
run. `--instance` was declared only on the top-level parser, so argparse saw it
after the subcommand and exited 2 with "unrecognized arguments: --instance" --
and since the whole point of the journal is that sessions end badly and write
nothing, a seat that followed its own instructions and got an error was
indistinguishable from the 90% that write nothing at all.

Every case below is about argument *placement*, which is why they live here and
not in `test_seat_journal.py`: storage is that file's subject, and every test in
it happened to use the one order that worked.
"""
from __future__ import annotations

import re
import shlex
import sys
from pathlib import Path

import pytest

import paths
from operator_cli import seat as cli
from operator_memory import journal

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "operator_kernel"))

import op  # noqa: E402


@pytest.fixture
def project(tmp_path, monkeypatch):
    """A registered project, so the catalog resolves and writes land."""
    home = tmp_path / "operator-home"
    (home / "projects").mkdir(parents=True)
    monkeypatch.setenv("COPILOT_OPERATOR_HOME", str(home))
    monkeypatch.setattr(paths, "OPERATOR_HOME", home, raising=False)

    cwd = tmp_path / "project"
    cwd.mkdir()
    guid = "11111111-2222-3333-4444-555555555555"
    (home / "projects" / guid).mkdir(parents=True)
    (home / "projects" / "catalog.csv").write_text(
        f'"{cwd}",{guid}\n', encoding="utf-8")
    assert paths.project_journal_file(cwd, "prism") is not None, (
        "the fixture did not register the project, so every case below would "
        "be asserting that an unregistered directory stores nothing")
    monkeypatch.chdir(cwd)
    monkeypatch.delenv(cli.INSTANCE_ENV, raising=False)
    return cwd


# ── the form the preamble advertises ────────────────────────────

def test_remember_takes_the_seat_after_the_subcommand(project, capsys):
    """The exact line the kernel puts in front of every unattended session."""
    assert cli.main(["remember", "--instance", "prism", "--kind", "gotcha",
                     "rotation is a rename"]) == 0
    assert "remembered" in capsys.readouterr().out
    assert journal.recall(project, "prism")[0]["text"] == "rotation is a rename"


def test_recall_takes_the_seat_after_the_subcommand(project, capsys):
    journal.remember(project, "prism", "gotcha", "tail by inode")
    assert cli.main(["recall", "--instance", "prism"]) == 0
    assert "tail by inode" in capsys.readouterr().out


def test_forget_takes_the_seat_after_the_subcommand(project, capsys):
    entry_id = journal.remember(project, "prism", "decision", "use threads")
    assert cli.main(["forget", "--instance", "prism", entry_id]) == 0
    assert cli.main(["--instance", "prism", "recall"]) == 0
    assert "use threads" not in capsys.readouterr().out


def test_the_session_number_survives_the_subcommand(project):
    assert cli.main(["remember", "--instance", "prism", "--session", "9",
                     "--kind", "gotcha", "x"]) == 0
    assert journal.recall(project, "prism")[0]["session"] == 9


# ── the form that already worked ────────────────────────────────

def test_the_seat_before_the_subcommand_is_not_overwritten(project):
    """The failure mode of the obvious fix.

    `_SubParsersAction` parses the subcommand into a *fresh* namespace and
    copies every key back over the one already parsed, so a subparser
    `--instance` carrying a real default of `None` would silently discard a
    value passed before the subcommand. Both orders have to work, not one at a
    time.
    """
    assert cli.main(["--instance", "prism", "--session", "4", "remember",
                     "--kind", "gotcha", "before"]) == 0
    entry = journal.recall(project, "prism")[0]
    assert entry["text"] == "before"
    assert entry["session"] == 4


def test_the_environment_still_supplies_the_seat(project, capsys, monkeypatch):
    monkeypatch.setenv(cli.INSTANCE_ENV, "prism")
    assert cli.main(["remember", "--kind", "decision", "chose sqlite"]) == 0
    assert cli.main(["recall"]) == 0
    assert "chose sqlite" in capsys.readouterr().out


def test_a_seat_named_twice_takes_the_one_nearest_the_text(project):
    """Ordinary argparse last-wins, in both directions.

    Asserted both ways round because one way round does not distinguish
    last-wins from "the subparser value always wins", and those two differ the
    moment someone reaches for a real default on the subparser again.
    """
    assert cli.main(["--instance", "other", "remember", "--instance", "prism",
                     "--kind", "gotcha", "after"]) == 0
    assert [e["text"] for e in journal.recall(project, "prism")] == ["after"]
    assert journal.recall(project, "other") == []

    assert cli.main(["--instance", "prism", "remember", "--instance", "other",
                     "--kind", "gotcha", "reversed"]) == 0
    assert [e["text"] for e in journal.recall(project, "other")] == ["reversed"]
    assert [e["text"] for e in journal.recall(project, "prism")] == ["after"]


# ── the preamble and the parser, bound together ─────────────────

def test_every_command_the_preamble_advertises_actually_runs(project,
                                                             tmp_path,
                                                             monkeypatch,
                                                             capsys):
    """The guard for the defect itself.

    The kernel writes these command lines into the preamble and the CLI parses
    them, and nothing connected the two: the advertised form could drift from
    the accepted form again with both files' own tests green.

    It *runs* them rather than parsing them, because parsing was the weaker
    claim in two ways both reviewers found. `--instance` is optional, so a
    preamble that stopped putting it after the verb would still have parsed and
    this test would have quietly stopped covering the defect; and the first
    version supplied the `remember` text operand itself, so a preamble that
    dropped the note would have been repaired here and passed. Nothing is added
    to the argv now, the flag's placement is asserted outright, and the write
    has to land.
    """
    monkeypatch.setattr(op, "RESTART_DIR", tmp_path / "restart")
    inst = op.Instance(display_name="prism")
    text = op.build_preamble("anvil:anvil", inst, has_journal=True)

    advertised = [shlex.split(segment)
                  for segment in re.findall(r"`([^`]*)`", text)
                  if segment.startswith("operator-seat ")]
    assert {argv[1] for argv in advertised} == {"remember", "recall"}, (
        "the preamble stopped naming both journal commands, so this test is "
        f"asserting nothing; saw: {advertised}")

    for argv in advertised:
        assert "--instance" in argv[2:], (
            f"the preamble no longer puts --instance after the subcommand, "
            f"which is the placement this whole file exists for: {argv}")
        assert cli.main(argv[1:]) == 0, f"the preamble advertises {argv}"
        capsys.readouterr()

    assert [e["text"] for e in journal.recall(project, inst.id)] == ["..."], (
        "the advertised remember parsed but wrote nothing, and a seat cannot "
        "tell that outcome from never having been told to write at all")

