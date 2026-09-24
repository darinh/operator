"""The cost recorder and the launch gate must agree on the seat key."""
from __future__ import annotations

import json

import exits
import op
import paths
from operator_cli import entry as cli


def _spend_file(home, key, amount):
    folder = home / "spend"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{key}.json").write_text(
        json.dumps({"amount": amount, "unit": "usd", "source": "probe"}),
        encoding="utf-8")


def test_the_cost_recorder_reads_the_same_key_the_gate_blocks_on(tmp_path):
    """The seat key is `instance.id`, and only that.

    `safe_instance_id` is not idempotent: 'a.b' becomes 'a-b-69f664' once and
    'a-b-69f664-5d16fe' twice. So a reader that tries both the display name and
    a sanitized name has two candidate filenames and no canonical one, and the
    gate and the cost recorder can disagree about which a seat spent under.

    A fixture named 'spend-ceiling' sanitizes to itself and cannot catch this.
    """
    display = "a.b"
    seat_id = op.safe_instance_id(display)
    assert seat_id != display, "pick a name that actually sanitizes"

    home = tmp_path / "home"
    home.mkdir()
    _spend_file(home, seat_id, 12.5)

    assert op.seat_spend(home, seat_id) == 12.5
    assert op.seat_spend(home, display) is None
    assert op.spend_blocks(home, seat_id, 5.0) is not None
    assert op.spend_blocks(home, display, 5.0) is None


def test_a_figure_filed_under_the_display_name_is_not_found(tmp_path):
    """The hazard the single key removes: a writer using the display name
    files a cost the ceiling will never read."""
    home = tmp_path / "home"
    home.mkdir()
    _spend_file(home, "a.b", 99.0)
    assert op.seat_spend(home, op.safe_instance_id("a.b")) is None


def test_a_session_ending_files_its_cost_under_the_seat_id(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    display = "a.b"
    seat_id = op.safe_instance_id(display)
    _spend_file(home, seat_id, 3.0)

    op.record_session_cost(home, instance=seat_id, session=1)

    lines = (home / "trace.jsonl").read_text(encoding="utf-8").splitlines()
    costs = [json.loads(line) for line in lines
             if json.loads(line).get("event") == "session_cost"]
    assert len(costs) == 1
    assert costs[0]["instance"] == seat_id
    assert costs[0]["amount"] == 3.0


# ── the handoff file: one module writes it, the same module reads it ──


def _registered(tmp_path, monkeypatch):
    """A project the catalog knows about, which is what a handoff needs."""
    monkeypatch.chdir(tmp_path)
    assert cli.main(["project", "register"]) == 0
    assert paths.catalog_guid(tmp_path).guid
    return tmp_path


def test_the_reader_finds_what_the_writer_wrote(tmp_path, monkeypatch):
    """The writer lived in `copilot-tools` and the reader here, so nothing
    ever checked that the two agreed on a location."""
    work = _registered(tmp_path, monkeypatch)
    landed = exits.write_handoff(work, "alpha", "did the thing",
                                 "do the next thing", "beware the cache")
    state = exits.handoff_state(work, "alpha")
    assert state.verdict == exits.HANDOFF_WAITING
    assert state.path == landed
    body = landed.read_text(encoding="utf-8")
    assert "did the thing" in body
    assert "do the next thing" in body
    assert "beware the cache" in body


def test_an_unregistered_project_is_refused_rather_than_guessed(tmp_path,
                                                                monkeypatch):
    """`None` is `project_handoff_file`'s answer for "not registered", and it
    must not become a path under the projects root itself."""
    monkeypatch.chdir(tmp_path)
    assert paths.catalog_guid(tmp_path).guid is None
    assert exits.write_handoff(tmp_path, "alpha", "s") is None


def test_the_replace_leaves_no_half_written_file_behind(tmp_path, monkeypatch):
    """The supervisor stats this path while polling, so a visible temp file is
    a handoff somebody reads as complete."""
    work = _registered(tmp_path, monkeypatch)
    landed = exits.write_handoff(work, "alpha", "first")
    exits.write_handoff(work, "alpha", "second")
    assert "second" in landed.read_text(encoding="utf-8")
    assert "first" not in landed.read_text(encoding="utf-8")
    assert list(landed.parent.glob("*.tmp")) == []


def test_the_restart_marker_is_the_one_the_supervisor_polls(tmp_path):
    """`supervisor.py` watches `instance.restart_marker` and nothing else."""
    marker = op.Instance("alpha").restart_marker
    assert not marker.exists()
    exits.request_restart("alpha")
    assert marker.exists()
