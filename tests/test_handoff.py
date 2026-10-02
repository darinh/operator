"""`operator handoff` -- the restart protocol every preamble advertises.

The kernel half of this is tested in `test_exits.py`. What is here is the
door: flags in, exit codes and printed lines out, and the two side effects a
operator depends on, which are the file on disk and the marker the supervisor
polls.
"""
from __future__ import annotations

import json

import op
import paths
import pytest
from operator_cli import entry as cli


def _project(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from operator_cli.project import ensure_registered
    assert ensure_registered(tmp_path)[0] == 0
    return tmp_path


def _operator(work, name="alpha"):
    import operators
    return operators.create(name, work)


def _seat(monkeypatch, record, *, pid=424242, token="win:100", session=7):
    """Make `record` the operator this process is inside."""
    import json
    import process_identity
    import process_tree
    op.Instance(record.id).custody_file.write_text(
        json.dumps({"pid": pid, "start": token, "session": session}),
        encoding="utf-8")
    monkeypatch.setattr(process_tree, "ancestry", lambda _pid: [pid])
    monkeypatch.setattr(process_identity, "process_start_token",
                        lambda asked: token if asked == pid else None)
    return session


def test_it_writes_the_file_and_asks_for_the_next_session(
        tmp_path, monkeypatch, capsys):
    """Key fact (2) of every launch preamble, end to end through the CLI."""
    work = _project(tmp_path, monkeypatch)
    record = _operator(work)
    _seat(monkeypatch, record)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    assert cli.main(["handoff", "--status", "landed the parser",
                     "--next", "wire the menu",
                     "--context", "budgets are tight"]) == 0
    out = capsys.readouterr().out
    written = paths.project_handoff_file(work, record.id)
    body = written.read_text(encoding="utf-8")
    assert "landed the parser" in body
    assert "wire the menu" in body
    assert "budgets are tight" in body
    assert f"handoff written to {written}" in out
    assert f"restart requested for {record.id}" in out
    marker = op.restart_marker_for(record.id)
    assert json.loads(marker.read_text(encoding="utf-8")) == {
        "id": record.id, "session": 7}
    assert not paths.project_handoff_file(work, "alpha").exists()
    assert not op.restart_marker_for("alpha").exists()


def test_the_operator_name_can_be_positional(tmp_path, monkeypatch, capsys):
    """Every other verb here takes a bare operator name, so this one does too."""
    work = _project(tmp_path, monkeypatch)
    record = _operator(work)
    _seat(monkeypatch, record)
    assert cli.main(["handoff", "alpha", "--status", "done"]) == 0
    out = capsys.readouterr().out
    assert paths.project_handoff_file(work, record.id).is_file()
    assert f"restart requested for {record.id}" in out
    assert op.restart_marker_for(record.id).exists()
    assert not paths.project_handoff_file(work, "alpha").exists()


def test_an_unknown_operator_exits_2_and_writes_nothing(
        tmp_path, monkeypatch, capsys):
    _project(tmp_path, monkeypatch)
    assert cli.main(["handoff", "--instance", "missing", "--status", "x"]) == 2
    assert "not inside an operator session" in capsys.readouterr().err
    assert list(paths.projects_root().rglob("*.md")) == []
    assert not op.restart_marker_for("missing").exists()


def test_an_unregistered_operator_repo_is_refused_and_the_fix_named(
        tmp_path, monkeypatch, capsys):
    """The file is written in the operator's repo, not the caller's cwd. An
    unregistered repo still has nowhere to put it."""
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    record = _operator(elsewhere)
    _seat(monkeypatch, record)
    monkeypatch.chdir(tmp_path)
    assert cli.main(["handoff", "--status", "x"]) == 1
    err = capsys.readouterr().err
    assert "not a registered project" in err
    assert "start an operator in that directory first" in err
    assert not op.restart_marker_for(record.id).exists()


def test_no_status_is_a_usage_error(tmp_path, monkeypatch, capsys):
    """Exit 2, so the preamble guard can tell "the CLI rejected this" from
    "the command ran and failed"."""
    _project(tmp_path, monkeypatch)
    assert cli.main(["handoff", "--instance", "alpha"]) == 2
    assert "Usage: operator handoff" in capsys.readouterr().err


def test_a_flag_left_without_a_value_names_itself(tmp_path, monkeypatch,
                                                  capsys):
    """`--status` swallowing the end of argv would otherwise read as an operator
    with no status, which reports the wrong problem."""
    _project(tmp_path, monkeypatch)
    assert cli.main(["handoff", "--instance", "alpha", "--status"]) == 2
    assert "--status needs a value" in capsys.readouterr().err


def test_it_can_checkpoint_without_ending_the_session(tmp_path, monkeypatch):
    """An operator about to start something long wants the file on disk and wants
    to keep running."""
    work = _project(tmp_path, monkeypatch)
    record = _operator(work)
    _seat(monkeypatch, record)
    assert cli.main(["handoff", "--status", "midway", "--no-restart"]) == 0
    assert paths.project_handoff_file(work, record.id).is_file()
    assert not op.restart_marker_for(record.id).exists()


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
    record = _operator(work)
    _seat(monkeypatch, record)
    assert cli.main(["handoff", "--instance=alpha", "--status=done",
                     "--no-restart"]) == 0
    assert paths.project_handoff_file(work, record.id).is_file()


def test_a_whitespace_only_status_is_not_a_status(tmp_path, monkeypatch,
                                                  capsys):
    """It passed the truthiness check and then wrote an empty section."""
    _project(tmp_path, monkeypatch)
    assert cli.main(["handoff", "--instance", "alpha", "--status", "   "]) == 2
    assert "Usage: operator handoff" in capsys.readouterr().err


def test_a_operator_name_that_escapes_the_handoff_directory_is_refused(
        tmp_path, monkeypatch, capsys):
    """`--instance ../escape` is not an operator, so it never becomes a path."""
    work = _project(tmp_path, monkeypatch)
    assert cli.main(["handoff", "--instance", "../escape",
                     "--status", "done"]) == 2
    assert "not inside an operator session" in capsys.readouterr().err
    assert list(paths.projects_root().rglob("escape.md")) == []
    assert not op.restart_marker_for("../escape").exists()
    assert work.is_dir()


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
    record = _operator(work)
    _seat(monkeypatch, record)
    assert cli.main(["handoff", "--instance", "alpha", "--status", "done",
                     "--context=--no-restart", "--no-restart"]) == 0
    body = paths.project_handoff_file(work, record.id).read_text(encoding="utf-8")
    assert "--no-restart" in body
    assert not op.restart_marker_for(record.id).exists()


def test_an_option_shaped_value_is_refused_however_it_is_spelled(tmp_path,
                                                                 monkeypatch,
                                                                 capsys):
    """Membership in the known flags was not enough. `--norestart` and
    `--no-restart=true` are not members, and both ended the session."""
    _project(tmp_path, monkeypatch)
    for tail in (["--status", "--instance=x"],
                 ["--status", "done", "--context", "--context=y"],
                 ["--status", "done", "--context", "--norestart"],
                 ["--status", "done", "--context", "--no-restart=true"]):
        assert cli.main(["handoff", "--instance", "alpha", *tail]) == 2, tail
        assert "needs a value" in capsys.readouterr().err
        assert not op.Instance("alpha").restart_marker.exists(), tail


def _expected_body(status: str, op_id: str) -> str:
    """The whole document a status-only handoff produces.

    The helper this replaces returned just the `## Status` section, split at
    the first blank line, and Reviewer A slipped a status carrying its own
    blank line plus trailing text past it. Comparing a section cannot see what
    is after the section; comparing the file can.
    """
    return f"# Handoff: {op_id}\n\n## Status\n\n{status}\n"


def _body(work, op_id: str) -> str:
    return paths.project_handoff_file(work, op_id).read_text(encoding="utf-8")


@pytest.mark.parametrize("status", [
    "- shipped the parser",
    "--no-restart",
    "-x",
    '- shipped "parser"',
])
def test_the_refusal_says_how_to_pass_a_value_that_looks_like_a_flag(
        status, tmp_path, monkeypatch, capsys):
    """A value that legitimately opens with a dash is a real thing to write,
    and telling that user "needs a value" when they supplied one is a lie.

    The message describes the shape rather than printing a literal to paste.
    Two rounds went on trying to make a paste-able form correct, and the last
    one still dropped embedded quotes silently. Nothing quotes correctly for
    every shell, so the instruction is the thing that is always true, and this
    test follows it rather than copying a string.
    """
    work = _project(tmp_path, monkeypatch)
    record = _operator(work)
    _seat(monkeypatch, record)
    assert cli.main(["handoff", "--instance", "alpha", "--status", status]) == 2
    err = capsys.readouterr().err
    assert "looks like an option" in err
    assert "--status=" in err
    assert "quoted for your shell" in err

    assert cli.main(["handoff", "--instance", "alpha", f"--status={status}",
                     "--no-restart"]) == 0
    assert _body(work, record.id) == _expected_body(status, record.id)


def test_handoff_bootstraps_from_the_shared_home_helper():
    from operator_cli import handoff, home
    assert handoff._bootstrap is home._bootstrap
