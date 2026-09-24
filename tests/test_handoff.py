"""`operator handoff` -- the restart protocol every preamble advertises.

The kernel half of this is tested in `test_exits.py`. What is here is the
door: flags in, exit codes and printed lines out, and the two side effects a
seat depends on, which are the file on disk and the marker the supervisor
polls.
"""
from __future__ import annotations

import op
import paths
from operator_cli import entry as cli


def _project(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert cli.main(["project", "register"]) == 0
    return tmp_path


def test_it_writes_the_file_and_asks_for_the_next_session(
        tmp_path, monkeypatch, capsys):
    """Key fact (2) of every launch preamble, end to end through the CLI."""
    work = _project(tmp_path, monkeypatch)
    marker = op.Instance("alpha").restart_marker
    assert cli.main(["handoff", "--instance", "alpha",
                     "--status", "landed the parser",
                     "--next", "wire the menu",
                     "--context", "budgets are tight"]) == 0
    out = capsys.readouterr().out
    assert "handoff written to" in out
    assert "restart requested for alpha" in out
    written = paths.project_handoff_file(work, "alpha")
    body = written.read_text(encoding="utf-8")
    assert "landed the parser" in body
    assert "wire the menu" in body
    assert "budgets are tight" in body
    assert marker.exists()


def test_the_seat_name_can_be_positional(tmp_path, monkeypatch):
    """Every other verb here takes a bare seat name, so this one does too."""
    work = _project(tmp_path, monkeypatch)
    assert cli.main(["handoff", "alpha", "--status", "done"]) == 0
    assert paths.project_handoff_file(work, "alpha").exists()


def test_an_unregistered_directory_is_refused_and_the_fix_named(
        tmp_path, monkeypatch, capsys):
    """A handoff has nowhere to go without a catalog entry. Saying so beats
    writing one where nothing will look for it."""
    monkeypatch.chdir(tmp_path)
    assert cli.main(["handoff", "--instance", "alpha", "--status", "x"]) == 1
    err = capsys.readouterr().err
    assert "not a registered project" in err
    assert "operator project register" in err
    assert not op.Instance("alpha").restart_marker.exists()


def test_no_status_is_a_usage_error(tmp_path, monkeypatch, capsys):
    """Exit 2, so the preamble guard can tell "the CLI rejected this" from
    "the command ran and failed"."""
    _project(tmp_path, monkeypatch)
    assert cli.main(["handoff", "--instance", "alpha"]) == 2
    assert "Usage: operator handoff" in capsys.readouterr().err


def test_a_flag_left_without_a_value_names_itself(tmp_path, monkeypatch,
                                                  capsys):
    """`--status` swallowing the end of argv would otherwise read as a seat
    with no status, which reports the wrong problem."""
    _project(tmp_path, monkeypatch)
    assert cli.main(["handoff", "--instance", "alpha", "--status"]) == 2
    assert "--status needs a value" in capsys.readouterr().err


def test_it_can_checkpoint_without_ending_the_session(tmp_path, monkeypatch):
    """A seat about to start something long wants the file on disk and wants
    to keep running."""
    work = _project(tmp_path, monkeypatch)
    assert cli.main(["handoff", "--instance", "alpha", "--status", "midway",
                     "--no-restart"]) == 0
    assert paths.project_handoff_file(work, "alpha").exists()
    assert not op.Instance("alpha").restart_marker.exists()


def test_help_names_every_flag_it_accepts(capsys):
    assert cli.main(["handoff", "--help"]) == 0
    out = capsys.readouterr().out
    for flag in ("--instance", "--status", "--next", "--context",
                 "--no-restart"):
        assert flag in out, flag


# ── argument handling the reviewers asked for ───────────────────


def test_an_unknown_option_is_refused_rather_than_ignored(tmp_path,
                                                          monkeypatch, capsys):
    """`--norestart` is a plausible typo for the switch that exists. Skipping
    it silently restarts the session the flag was meant to preserve."""
    _project(tmp_path, monkeypatch)
    assert cli.main(["handoff", "--instance", "alpha", "--status", "done",
                     "--norestart"]) == 2
    assert "unknown option --norestart" in capsys.readouterr().err
    assert not op.Instance("alpha").restart_marker.exists()


def test_flag_equals_value_is_accepted(tmp_path, monkeypatch):
    """Every other verb on this entry point takes `--name=value`."""
    work = _project(tmp_path, monkeypatch)
    assert cli.main(["handoff", "--instance=alpha", "--status=done",
                     "--no-restart"]) == 0
    assert paths.project_handoff_file(work, "alpha").exists()


def test_a_whitespace_only_status_is_not_a_status(tmp_path, monkeypatch,
                                                  capsys):
    """It passed the truthiness check and then wrote an empty section."""
    _project(tmp_path, monkeypatch)
    assert cli.main(["handoff", "--instance", "alpha", "--status", "   "]) == 2
    assert "Usage: operator handoff" in capsys.readouterr().err


def test_a_seat_name_that_escapes_the_handoff_directory_is_refused(
        tmp_path, monkeypatch, capsys):
    """`--instance ../escape` addressed a file outside `handoff/`."""
    _project(tmp_path, monkeypatch)
    assert cli.main(["handoff", "--instance", "../escape",
                     "--status", "done"]) == 2
    assert "not usable" in capsys.readouterr().err


def test_a_second_positional_is_refused(tmp_path, monkeypatch, capsys):
    """Two bare words means one of them was meant to be a flag value."""
    _project(tmp_path, monkeypatch)
    assert cli.main(["handoff", "alpha", "beta", "--status", "done"]) == 2
    assert "unexpected argument" in capsys.readouterr().err


def test_a_value_flag_does_not_swallow_the_next_option(tmp_path, monkeypatch,
                                                       capsys):
    """The regression the first round of review fixes introduced.

    `--context --no-restart` took the switch as the context text, left the
    switch unparsed, and restarted the session the user had just asked to
    keep. Worse than the typo it was fixed alongside, because the flag was
    spelled correctly.
    """
    _project(tmp_path, monkeypatch)
    assert cli.main(["handoff", "--instance", "alpha", "--status", "done",
                     "--context", "--no-restart"]) == 2
    assert "--context needs a value" in capsys.readouterr().err
    assert not op.Instance("alpha").restart_marker.exists()


def test_a_status_that_looks_like_a_flag_is_not_taken_as_one(tmp_path,
                                                             monkeypatch,
                                                             capsys):
    """The same rule one flag over, because `--status --no-restart` wrote the
    switch as the status and ended the session."""
    _project(tmp_path, monkeypatch)
    assert cli.main(["handoff", "--instance", "alpha", "--status",
                     "--no-restart"]) == 2
    assert "--status needs a value" in capsys.readouterr().err
    assert not op.Instance("alpha").restart_marker.exists()


def test_joined_syntax_is_how_you_mean_an_option_literally(tmp_path,
                                                           monkeypatch):
    """Refusing the separated form needs an escape hatch, or text that happens
    to look like a flag becomes unsayable."""
    work = _project(tmp_path, monkeypatch)
    assert cli.main(["handoff", "--instance", "alpha", "--status", "done",
                     "--context=--no-restart", "--no-restart"]) == 0
    body = paths.project_handoff_file(work, "alpha").read_text(encoding="utf-8")
    assert "--no-restart" in body
    assert not op.Instance("alpha").restart_marker.exists()
