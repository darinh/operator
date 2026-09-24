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
    """Ordinary argparse last-wins, stated so the precedence is not a surprise."""
    assert cli.main(["--instance", "other", "remember", "--instance", "prism",
                     "--kind", "gotcha", "x"]) == 0
    assert journal.recall(project, "prism")
    assert not journal.recall(project, "other")


# ── the preamble and the parser, bound together ─────────────────

def test_every_command_the_preamble_advertises_actually_parses(tmp_path,
                                                               monkeypatch):
    """The guard for the defect itself.

    The kernel writes these command lines into the preamble and the CLI parses
    them, and nothing connected the two: the advertised form could drift from
    the accepted form again with both files' own tests green. This reads the
    real preamble, lifts every `operator-seat` line out of it, and feeds it to
    the real parser.
    """
    monkeypatch.setattr(op, "RESTART_DIR", tmp_path / "restart")
    text = op.build_preamble("anvil:anvil", op.Instance(display_name="prism"),
                             has_journal=True)

    advertised = [line.strip(' .,"')
                  for line in text.replace("`", "\n").splitlines()
                  if line.strip().startswith("operator-seat ")]
    assert len(advertised) >= 2, (
        "the preamble stopped naming the journal commands, so this test is "
        f"asserting nothing; saw: {advertised}")

    parser = cli.build_parser()
    for command in advertised:
        argv = command.split()[1:]
        if argv and argv[0] == "remember":
            argv += ["a note"]  # the quoted text, which split() cannot give
        parser.parse_args(argv)
