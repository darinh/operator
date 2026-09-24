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

#: A command example the preamble offers: a backtick span whose first token
#: claims to be this program. Deliberately permissive about everything after
#: that -- a misspelled verb, a quoted verb, flags before the verb, a
#: misspelled *name* -- because the failure mode of every earlier version of
#: this scan was not seeing a broken example rather than misjudging one.
#:
#: Extraction and judgement were the same rule for two rounds, and both
#: reviewers found the same consequence independently: a command outside the
#: rule's grammar disappeared from the scan and from the count of what the scan
#: should have found, so the two agreed with each other about nothing. They are
#: separate now. This says what to look at; the test says what is acceptable.
def advertised_commands(text: str) -> "list[list[str]]":
    """Every `operator-seat` command example the preamble offers, tokenised.

    Only backtick spans are read. Earlier versions also scanned the prose, to
    catch an example that escaped its formatting, and that is what made the
    guard cry wolf: a sentence naming the program, a seat id containing it, an
    assignment mentioning it, and a note whose own text quoted a command were
    all reported as commands nobody ran. None of them is. The contract here is
    over examples a reader would copy, and a preamble that stops offering any
    fails the verb check below rather than passing quietly.
    """
    commands = []
    for segment in re.findall(r"`([^`]*)`", text):
        argv = shlex.split(segment.strip())
        if argv and argv[0].startswith("operator-seat"):
            commands.append(argv)
    return commands


def test_the_extraction_hands_back_the_examples_that_are_wrong():
    """The control for the helper above, built from what broke it in review.

    Each of these was found passing. One leading space inside the backticks
    dropped a command before tokenising. A prefix match accepted an executable
    that was not this program, which then ran it anyway because `cli.main` is
    handed `argv[1:]` and never sees the name it was called by. And a scan that
    required a well-formed verb could not see `remembers`, a quoted verb, or a
    flag before the verb -- the three shapes a broken example actually takes.

    All of them come back from here now. Judging them is the next test's job,
    and that is the whole point: a scan that only returns valid commands cannot
    report an invalid one.
    """
    padded = 'write it with ` operator-seat remember --instance x --kind gotcha "n"`.'
    assert advertised_commands(padded) == [
        ["operator-seat", "remember", "--instance", "x", "--kind", "gotcha", "n"]]

    assert advertised_commands("`operator-seat-bogus recall --instance x`") == [
        ["operator-seat-bogus", "recall", "--instance", "x"]]
    assert advertised_commands("`operator-seat remembers --instance x`") == [
        ["operator-seat", "remembers", "--instance", "x"]]
    assert advertised_commands('`operator-seat "remember" --instance x`') == [
        ["operator-seat", "remember", "--instance", "x"]]
    assert advertised_commands("`operator-seat --instance x remember`") == [
        ["operator-seat", "--instance", "x", "remember"]]


def test_the_extraction_does_not_fire_on_a_preamble_that_is_fine():
    """The other half, because an over-strict guard is one somebody weakens.

    Every one of these was failed by a previous revision, and not one of them
    is a command that went unrun. The last is the sharpest: a note whose own
    text quotes a command is still one command, and a scan that reads the prose
    cannot tell the difference.
    """
    assert advertised_commands("The operator-seat program records claims.") == []
    assert advertised_commands("The operator-seat recall command reads notes.") == []
    assert advertised_commands("`operator-seat recall --instance x`, from a "
                               "program named operator-seat.") == [
        ["operator-seat", "recall", "--instance", "x"]]
    assert advertised_commands("`operator-seat recall --instance "
                               "operator-seat-prism`") == [
        ["operator-seat", "recall", "--instance", "operator-seat-prism"]]
    assert advertised_commands('`operator-seat remember --instance x --kind '
                               'gotcha "operator-seat recall needs a seat"`') == [
        ["operator-seat", "remember", "--instance", "x", "--kind", "gotcha",
         "operator-seat recall needs a seat"]]


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
    preamble that stopped putting it after the verb would still have parsed;
    and the first version supplied the `remember` text operand itself, so a
    preamble that dropped the note would have been repaired here and passed.

    The placement is required of *some* advertised command rather than all of
    them, because both orders work now and an extra example in the other one is
    not a defect. The regression this file exists for is the preamble ceasing
    to offer the post-subcommand form at all.

    Every advertised recall is run again after the write, rather than the first
    one. In argv order recall runs before the write it is meant to read, so an
    exit code was all the first version could check -- and an advertised
    `--per-kind 0` returned 0 while showing the seat nothing.
    """
    monkeypatch.setattr(op, "RESTART_DIR", tmp_path / "restart")
    inst = op.Instance(display_name="prism")
    text = op.build_preamble("anvil:anvil", inst, has_journal=True)

    advertised = advertised_commands(text)
    verbs = set()
    for argv in advertised:
        assert argv[0] == "operator-seat", (
            f"the preamble advertises {argv[0]!r}, which is not this program. "
            f"`cli.main` is handed argv[1:] and would run the real one anyway")
        verbs.add(_verb(argv))
        assert _run(argv) == 0, f"the preamble advertises {argv}"
        capsys.readouterr()

    assert verbs == {"remember", "recall"}, (
        "the preamble stopped naming both journal commands, so this test is "
        f"asserting nothing; saw: {advertised}")
    assert any("--instance" in argv[2:] for argv in advertised), (
        f"no advertised command puts --instance after the subcommand any more, "
        f"which is the placement this whole file exists for: {advertised}")

    assert [e["text"] for e in journal.recall(project, inst.id)] == ["..."], (
        "the advertised remember parsed but wrote nothing, and a seat cannot "
        "tell that outcome from never having been told to write at all")

    for argv in advertised:
        if _verb(argv) != "recall":
            continue
        assert _run(argv) == 0
        assert "..." in capsys.readouterr().out, (
            f"{argv} exited 0 and showed the seat nothing back, which is the "
            f"outcome it cannot distinguish from an empty journal")


def _verb(argv: "list[str]") -> str:
    """The subcommand argparse finds in an advertised command, wherever it is.

    Read from the parser rather than from `argv[1]`, so that an example whose
    flags come before the verb is judged on what it does rather than skipped
    for the shape it is in.
    """
    return _parse(argv).command


def _parse(argv: "list[str]"):
    try:
        return cli.build_parser().parse_args(argv[1:])
    except SystemExit as refused:  # argparse prints its own reason to stderr
        raise AssertionError(
            f"the preamble advertises {argv}, which this program refuses: "
            f"exit {refused.code}") from None


def _run(argv: "list[str]") -> int:
    try:
        return cli.main(argv[1:])
    except SystemExit as refused:
        raise AssertionError(
            f"the preamble advertises {argv}, which this program refuses: "
            f"exit {refused.code}") from None

