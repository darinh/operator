"""The sandbox is a real git repo whose home is not the owner's live fleet."""
from __future__ import annotations

import sys
from pathlib import Path

from operator_bench.world import git, kill, make_world, refs, spawn, wait


def test_disposable_home_is_not_the_real_operator_home(tmp_path):
    world = make_world(tmp_path)
    real = (Path.home() / ".operator").resolve()
    home = world.home.resolve()
    assert home != real
    assert real not in home.parents


def test_for_each_ref_changes_when_the_repo_commits(tmp_path):
    world = make_world(tmp_path)
    before = refs(world.repo)
    assert "refs/heads/main" in before
    assert "refs/remotes/origin/main" in before
    (world.repo / "README").write_text("second\n", encoding="ascii")
    git(world.repo, "add", "-A")
    git(world.repo, "commit", "-m", "second")
    after = refs(world.repo)
    assert before != after


def test_spawned_child_writes_only_into_the_disposable_home(tmp_path):
    world = make_world(tmp_path)
    real_marker = Path.home() / ".operator" / "bench-world-marker"
    script = (
        "import os, pathlib\n"
        "home = pathlib.Path(os.environ['COPILOT_OPERATOR_HOME'])\n"
        "(home / 'marker').write_text(str(home), encoding='utf-8')\n"
    )
    proc = spawn(world, [sys.executable, "-c", script])
    assert wait(proc, 15) == 0
    assert (world.home / "marker").read_text(encoding="utf-8") == str(world.home)
    assert not real_marker.exists()


def test_kill_stops_a_live_child(tmp_path):
    world = make_world(tmp_path)
    proc = spawn(world, [sys.executable, "-c", "import time; time.sleep(60)"])
    assert proc.poll() is None
    kill(proc)
    assert proc.poll() is not None
