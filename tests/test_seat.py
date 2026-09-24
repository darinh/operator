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

#: An advertised command example: the CLI's name, *however it is spelled*, with
#: one of its verbs after it. A misspelling matches deliberately, so that
#: `operator-seat-bogus recall` is extracted and rejected by name below rather
#: than never being seen -- the failure mode of every earlier version of this
#: scan was skipping, not misjudging.
#:
#: A verb is required, so prose naming the program, and a seat id with the name
#: inside it, are not miscounted as commands nobody ran. That was the cost of
#: counting the bare substring: three legitimate preambles failed.
ADVERTISED = re.compile(r"operator-seat\S*\s+(?:remember|recall|forget)\b")


def advertised_commands(text: str) -> "list[list[str]]":
    """Every `operator-seat` command example the preamble offers, tokenised.

    A helper rather than three lines inline, so the extraction itself can be
    put under test below. It failed three times in review, each time by
    *skipping* rather than by misjudging: `str.split` could not carry a quoted
    operand; matching an unstripped segment dropped a command indented by one
    space; and matching a prefix accepted a command whose executable was not
    this one.

    So it counts rather than filters. Every command-shaped mention anywhere in
    the preamble has to come back from here, which is what makes a mention
    outside backticks a failure instead of a blind spot.
    """
    segments = [segment.strip() for segment in re.findall(r"`([^`]*)`", text)]
    commands = [shlex.split(segment) for segment in segments
                if ADVERTISED.match(segment)]
    assert len(commands) == len(ADVERTISED.findall(text)), (
        f"the preamble advertises {len(ADVERTISED.findall(text))} command(s) "
        f"and this could reach {len(commands)}: one of them is outside "
        f"backticks, so nothing below runs it. Segments seen: {segments}")
    return commands


def test_the_extraction_cannot_quietly_skip_an_advertised_command():
    """The control for the helper above, built from what broke it in review.

    One leading space inside the backticks was enough: the command was dropped
    before tokenising, the surviving example supplied the same verb, and the
    test below stayed green while the preamble advertised a command that exits
    2. A prefix match was enough the next time, because `cli.main` is handed
    `argv[1:]` and never sees the name it was called by.
    """
    padded = 'write it with ` operator-seat remember --instance x --kind gotcha "n"`.'
    assert advertised_commands(padded) == [
        ["operator-seat", "remember", "--instance", "x", "--kind", "gotcha", "n"]]

    outside = ('run operator-seat remember yourself, or '
               '`operator-seat recall --instance x`.')
    with pytest.raises(AssertionError,
                       match=r"advertises 2 command\(s\) and this could reach 1"):
        advertised_commands(outside)

    # Handed back rather than dropped, so the caller rejects it by name.
    assert advertised_commands("`operator-seat-bogus recall --instance x`") == [
        ["operator-seat-bogus", "recall", "--instance", "x"]]


def test_the_extraction_does_not_fire_on_a_preamble_that_is_fine():
    """The other half, because an over-strict guard is one somebody weakens.

    Counting the bare name failed all three of these, none of which is a
    command nobody ran. A test that cries wolf on a legitimate edit teaches the
    next agent to relax it rather than to fix the preamble.
    """
    assert advertised_commands("The operator-seat program records claims.") == []
    assert advertised_commands("`operator-seat recall --instance x`, a "
                               "program named operator-seat.") == [
        ["operator-seat", "recall", "--instance", "x"]]
    assert advertised_commands("`operator-seat recall --instance "
                               "operator-seat-prism`") == [
        ["operator-seat", "recall", "--instance", "operator-seat-prism"]]


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
    dropped the note would have been repaired here and passed.

    The recall is run a second time at the end rather than only in argv order.
    In argv order it runs before the write it is supposed to read, so checking
    its exit code was all the first version could do -- and an advertised
    `--per-kind 0` returned 0 while showing the seat nothing.
    """
    monkeypatch.setattr(op, "RESTART_DIR", tmp_path / "restart")
    inst = op.Instance(display_name="prism")
    text = op.build_preamble("anvil:anvil", inst, has_journal=True)

    advertised = advertised_commands(text)
    assert all(len(argv) > 1 for argv in advertised), (
        f"the preamble advertises a bare command with no subcommand: "
        f"{advertised}")
    assert {argv[1] for argv in advertised} == {"remember", "recall"}, (
        "the preamble stopped naming both journal commands, so this test is "
        f"asserting nothing; saw: {advertised}")

    for argv in advertised:
        assert argv[0] == "operator-seat", (
            f"the preamble advertises {argv[0]!r}, which is not this program. "
            f"`cli.main` is handed argv[1:] and would run the real one anyway")
        assert "--instance" in argv[2:], (
            f"the preamble no longer puts --instance after the subcommand, "
            f"which is the placement this whole file exists for: {argv}")
        assert cli.main(argv[1:]) == 0, f"the preamble advertises {argv}"
        capsys.readouterr()

    assert [e["text"] for e in journal.recall(project, inst.id)] == ["..."], (
        "the advertised remember parsed but wrote nothing, and a seat cannot "
        "tell that outcome from never having been told to write at all")

    recall = next(argv for argv in advertised if argv[1] == "recall")
    assert cli.main(recall[1:]) == 0
    assert "..." in capsys.readouterr().out, (
        f"{recall} exited 0 and showed the seat nothing back, which is the "
        f"outcome it cannot distinguish from an empty journal")

