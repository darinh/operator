"""`operator-recover` — the command a human runs after the machine comes back.

Thin by design: `supervisor_control` decides which seats were running when the
machine went down and what continuing one means, and this parses arguments and
calls in. So these tests are about the shape of the conversation rather than
the decision -- what it lists, what it refuses to do without being asked, and
whether one seat that cannot come back stops the others.

The default is deliberately a *list*. A reboot takes the whole fleet, so
`--all` is the common case, and a command whose bare form silently started
eight agents would be one people run once.
"""
from __future__ import annotations

import json
import subprocess
import sys

import pytest

import op
from operator_cli import recover


@pytest.fixture
def cli(monkeypatch, tmp_path):
    """The CLI with its decisions stubbed, so only its own behaviour is under test."""
    import supervisor_control

    listed, recovered = [], []
    monkeypatch.setattr(supervisor_control, "recoverable_instances",
                        lambda: list(listed))
    monkeypatch.setattr(supervisor_control, "recover_loop",
                        lambda inst: recovered.append(inst.display_name) or 0)
    monkeypatch.setattr(op, "OPERATOR_HOME", tmp_path)
    return {"listed": listed, "recovered": recovered}


def _seat(name):
    return op.Instance(name)


# ── the default is to report, not to act ─────────────────────────


def test_with_nothing_to_do_it_says_so(cli, capsys):
    assert recover.main([]) == 0
    assert "No seats need recovering" in capsys.readouterr().out


def test_it_lists_rather_than_starting_anything(cli, capsys):
    """A bare `operator-recover` that silently started eight agents would be a
    command people run once."""
    cli["listed"].extend([_seat("alpha"), _seat("bravo")])
    assert recover.main([]) == 0
    out = capsys.readouterr().out
    assert "alpha" in out and "bravo" in out
    assert cli["recovered"] == [], "listing started a supervisor"


def test_the_listing_says_how_to_act_on_it(cli, capsys):
    cli["listed"].append(_seat("alpha"))
    recover.main([])
    assert "--all" in capsys.readouterr().out


# ── acting ───────────────────────────────────────────────────────


def test_all_recovers_every_seat(cli):
    cli["listed"].extend([_seat("alpha"), _seat("bravo")])
    assert recover.main(["--all"]) == 0
    assert cli["recovered"] == ["alpha", "bravo"]


def test_a_named_seat_is_recovered_alone(cli):
    cli["listed"].extend([_seat("alpha"), _seat("bravo")])
    assert recover.main(["bravo"]) == 0
    assert cli["recovered"] == ["bravo"]


def test_naming_a_seat_does_not_require_it_to_be_in_the_list(cli, monkeypatch):
    """The list is a convenience, not the authority. A human who knows which
    seat they mean should not have to argue with the listing about it -- and
    `recover_loop` refuses for its own reasons anyway."""
    assert recover.main(["somebody-else"]) == 0
    assert cli["recovered"] == ["somebody-else"]


# ── one failure must not decide the rest ─────────────────────────


def test_one_seat_that_cannot_come_back_does_not_strand_the_others(
        cli, monkeypatch, capsys):
    """A deleted working directory is a property of that seat, not of the
    machine. After a reboot the whole fleet is in this list, so a sweep that
    stopped at the first refusal would recover almost none of it."""
    import supervisor_control

    def refuse_bravo(inst):
        if inst.display_name == "bravo":
            return 1
        cli["recovered"].append(inst.display_name)
        return 0

    monkeypatch.setattr(supervisor_control, "recover_loop", refuse_bravo)
    cli["listed"].extend([_seat("alpha"), _seat("bravo"), _seat("charlie")])

    assert recover.main(["--all"]) == 1, "a partial sweep reported success"
    assert cli["recovered"] == ["alpha", "charlie"]
    assert "2 of 3" in capsys.readouterr().out


def test_a_sweep_that_recovers_everything_reports_success(cli, capsys):
    cli["listed"].extend([_seat("alpha"), _seat("bravo")])
    assert recover.main(["--all"]) == 0
    assert "2 of 2" in capsys.readouterr().out


# ── the parser ───────────────────────────────────────────────────


def test_the_home_can_be_relocated():
    """Every other command here takes `--home`; a recovery tool that could only
    ever address the real one would be untestable and undemonstrable."""
    args = recover.build_parser().parse_args(["--home", "/somewhere"])
    assert args.home == "/somewhere"


def test_a_name_and_all_are_both_optional():
    args = recover.build_parser().parse_args([])
    assert args.name is None and args.all is False


def test_the_home_is_settled_before_the_kernel_resolves_it(tmp_path):
    """The bug this found, and the reason it is driven as a real process.

    `config.py` resolves `OPERATOR_HOME` at import and derives `RESTART_DIR`
    from it there, so a `--home` applied *after* the kernel is imported
    reaches nothing. The first version of this command did exactly that, and
    the only way it showed was running it: pointed at a fixture holding two
    planted seats, it listed the developer's eleven real ones.

    A subprocess on purpose, because in-process the kernel is already
    imported and the ordering under test has already happened. Read-only --
    it lists and starts nothing -- so a regression reports rather than acts.
    """
    restart = tmp_path / "restart"
    restart.mkdir(parents=True)
    (restart / "planted.managed").write_text('{"session": "planted"}',
                                             encoding="utf-8")
    (restart / "planted.loopargs.json").write_text(
        json.dumps({"user_args": ["--agent", "a"], "cwd": str(tmp_path)}),
        encoding="utf-8")

    result = subprocess.run(
        [sys.executable, "-m", "operator_cli.recover", "--home", str(tmp_path)],
        capture_output=True, encoding="utf-8", errors="replace", timeout=120)

    assert "planted" in result.stdout, (
        f"--home did not reach the kernel; it read somewhere else:\n"
        f"{result.stdout}\n{result.stderr}")
    lines = [ln.strip() for ln in result.stdout.splitlines()
             if ln.startswith("  ") and ln.strip()
             and not ln.strip().startswith(("Bring", "Or one"))]
    assert lines == ["planted"], (
        f"the command listed seats from another home entirely: {lines}")
