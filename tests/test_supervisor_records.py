"""Startup records are the live invocation, written in one order."""
from __future__ import annotations

import inspect
import json

import op


def test_publishing_records_the_live_invocation(tmp_path, monkeypatch):
    monkeypatch.setattr(op, "RESTART_DIR", tmp_path)
    monkeypatch.chdir(tmp_path)
    operator = op.Instance("alpha")
    operator.loop_startup_file.write_text("starting", encoding="utf-8")
    op._publish_supervisor_records(operator, ["--yolo"])
    recorded = json.loads(operator.loop_args_file.read_text(encoding="utf-8"))
    assert recorded["user_args"] == ["--yolo"]
    assert recorded["cwd"] == str(tmp_path)
    assert operator.loop_pid_file.read_text(encoding="utf-8").splitlines()[0].isdigit()
    assert not operator.loop_startup_file.exists()


def test_publishing_takes_the_instance_and_its_arguments():
    assert tuple(inspect.signature(op._publish_supervisor_records).parameters) == (
        "instance", "user_args")
