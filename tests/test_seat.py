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

    Only backtick spans are read, and the span has to name the program before
    anything is parsed. Both halves were found by review. Earlier versions
    scanned the prose too, to catch an example that escaped its formatting, and
    that is what made the guard cry wolf: a sentence naming the program, a
    sentence naming its `recall` command, a seat id containing the name, an
    assignment mentioning it, and a note whose own text quoted a command were
    all reported as commands nobody ran. And shell-parsing every span before
    asking whether it was a command failed on an ordinary apostrophe.

    A span that does name the program must be one shell command a reader can
    copy: unbalanced backticks mean the spans below are paired with the wrong
    partners, and a newline inside one means the shell would run two commands
    where this runs one -- `shlex` treats the break as ordinary whitespace, so
    an advertised note on its own line was being handed to a command that, as
    typed, does not receive it.
    """
    assert text.count("`") % 2 == 0, (
        "the preamble has an odd number of backticks, so the spans below are "
        "paired with the wrong partners and an example can hide between them")
    commands = []
    for segment in re.findall(r"`([^`]*)`", text):
        stripped = segment.strip()
        if not stripped.startswith("operator-seat"):
            continue
        assert "\n" not in segment, (
            f"the preamble advertises a command broken across lines, which a "
            f"shell reads as two: {segment!r}")
        try:
            commands.append(shlex.split(stripped))
        except ValueError as unparsable:
            raise AssertionError(
                f"the preamble advertises {stripped!r}, which is not one "
                f"shell command: {unparsable}") from None
    return commands


def assert_no_unformatted_command(text: str) -> None:
    """No prose telling a seat to *run* this program outside a code span.

    The narrowest form of a check this file gave up once and had handed back by
    the reviewer who had argued against the broad one. Scanning prose for
    mentions produced five false alarms; scanning it for an imperative produces
    none of them, and still catches the realistic edit -- an instruction added
    as a sentence rather than as an example, which no test then runs. The
    remedy asked for is formatting, not parsing: put it in backticks and
    everything above will check it.
    """
    prose = re.sub(r"`[^`]*`", " ", text)
    loose = re.findall(r"run operator-seat[^.]*", prose)
    assert not loose, (
        f"the preamble tells a seat to run a command that is not in backticks, "
        f"so nothing runs it here: {loose}")


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

    with pytest.raises(AssertionError, match="not one shell command"):
        advertised_commands("`operator-seat remember --kind gotcha \"open`")
    with pytest.raises(AssertionError, match="broken across lines"):
        advertised_commands('`operator-seat remember --kind gotcha\n"n"`')
    with pytest.raises(AssertionError, match="odd number of backticks"):
        advertised_commands("`operator-seat recall --instance x` and `more")
    with pytest.raises(AssertionError, match="not in backticks"):
        assert_no_unformatted_command(
            "For a decision, run operator-seat remember --instance x "
            "--kind decision.")


def test_the_extraction_does_not_fire_on_a_preamble_that_is_fine():
    """The other half, because an over-strict guard is one somebody weakens.

    Every one of these was failed by a previous revision, and not one of them
    is a command that went unrun. The note whose own text quotes a command is
    the sharpest: it is still one command, and a scan that reads the prose
    cannot tell the difference. The apostrophe is the cheapest: shell-parsing
    every span before asking whether it named the program made an ordinary
    possessive raise.

    The last two go through the real `build_preamble`, because the five above
    are strings this file made up and a control built only from those proves
    the helper agrees with its author.
    """
    assert advertised_commands("The operator-seat program records claims.") == []
    assert advertised_commands("The operator-seat recall command reads notes.") == []
    assert advertised_commands("`session's notes`") == []
    assert advertised_commands("`operator-seat recall --instance x`, from a "
                               "program named operator-seat.") == [
        ["operator-seat", "recall", "--instance", "x"]]
    assert advertised_commands('`operator-seat remember --instance x --kind '
                               'gotcha "operator-seat recall needs a seat"`') == [
        ["operator-seat", "remember", "--instance", "x", "--kind", "gotcha",
         "operator-seat recall needs a seat"]]


@pytest.mark.parametrize("display_name, assignment", [
    ("operator-seat-prism", ""),
    ("prism", "Use operator-seat to journal what you find."),
    ("prism", "Investigate operator-seat argument ordering."),
])
def test_a_real_preamble_offers_exactly_its_two_commands(display_name,
                                                         assignment,
                                                         tmp_path, monkeypatch):
    """The same half, through the kernel rather than through strings.

    A seat id containing the program's name, and an assignment mentioning it,
    each failed a previous revision. They are built here by `build_preamble`
    rather than typed, because a control made only of this file's own strings
    establishes that the helper agrees with whoever wrote them.
    """
    monkeypatch.setattr(op, "RESTART_DIR", tmp_path / "restart")
    text = op.build_preamble("anvil:anvil",
                             op.Instance(display_name=display_name),
                             assignment=assignment, has_journal=True)
    assert_no_unformatted_command(text)
    assert [argv[1] for argv in advertised_commands(text)] == ["recall",
                                                              "remember"]


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
    assert_no_unformatted_command(text)
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
    assert any("--instance" in _after_the_verb(argv) for argv in advertised), (
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


def _after_the_verb(argv: "list[str]") -> "list[str]":
    """The part of an advertised command that follows its subcommand.

    `argv[2:]` stood here and was not the same thing. `operator-seat --session
    1 --instance prism recall` has `--instance` inside `argv[2:]` while putting
    it *before* the verb, so the placement assertion this file exists for
    passed on a preamble that had abandoned the placement entirely.

    The walk is checked against argparse rather than trusted: the two have to
    name the same subcommand, or the invariant being asserted is this helper's
    opinion.
    """
    index = 1
    while index < len(argv) and argv[index].startswith("--"):
        index += 1 if "=" in argv[index] else 2
    assert index < len(argv) and argv[index] == _verb(argv), (
        f"this walk and argparse disagree about where the subcommand is in "
        f"{argv}, so the placement check below would be asserting nothing")
    return argv[index + 1:]


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

