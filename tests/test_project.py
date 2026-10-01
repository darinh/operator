"""Registration is what start uses so a handoff has a project to land in."""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import paths
from operator_cli.project import ensure_registered


def test_register_prints_a_guid_the_existing_reader_accepts(tmp_path, monkeypatch):
    cwd = tmp_path / "repo"
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    rc, guid, created = ensure_registered()
    assert rc == 0 and created
    assert paths.guid_is_usable(guid)
    found = paths.catalog_guid(cwd)
    assert found.guid == guid
    assert not found.undecided


def test_registering_twice_does_not_add_a_second_row(tmp_path, monkeypatch):
    cwd = tmp_path / "repo"
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    rc, first, created = ensure_registered()
    assert rc == 0 and created
    rc, again, created = ensure_registered()
    assert rc == 0 and not created and again == first
    catalog = paths.project_catalog_path()
    with open(catalog, "r", encoding="utf-8", errors="replace", newline="") as fh:
        rows = [row for row in paths.catalog_rows(fh) if row]
    assert len(rows) == 1
    assert paths.catalog_guid(cwd).guid == first


def test_register_of_a_missing_path_fails(tmp_path):
    rc, guid, created = ensure_registered(str(tmp_path / "nope"))
    assert (rc, guid, created) == (2, "", False)


def test_register_reuses_guid_when_samefile_says_so(tmp_path, monkeypatch):
    first = tmp_path / "one"
    alias = tmp_path / "two"
    first.mkdir()
    alias.mkdir()
    monkeypatch.chdir(first)
    rc, guid, created = ensure_registered()
    assert rc == 0 and created

    def fake_samefile(left, right):
        try:
            pair = {str(Path(left).resolve()), str(Path(right).resolve())}
        except (OSError, ValueError):
            return False
        return {str(first.resolve()), str(alias.resolve())} <= pair or (
            Path(left).resolve() == Path(right).resolve())

    monkeypatch.setattr(os.path, "samefile", fake_samefile)
    rc, again, created = ensure_registered(str(alias))
    assert rc == 0 and not created and again == guid
    assert paths.catalog_guid(alias).guid == guid
    assert paths.catalog_guid(first).guid == guid


def test_two_processes_registering_keep_both_rows(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / "projects").mkdir(parents=True)
    monkeypatch.setenv("COPILOT_OPERATOR_HOME", str(home))
    a = tmp_path / "a"
    b = tmp_path / "b"
    a.mkdir()
    b.mkdir()
    gate = tmp_path / "go"
    root = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    env["COPILOT_OPERATOR_HOME"] = str(home)
    env["OPERATOR_CATALOG_PAUSE"] = "0.4"
    env["PYTHONPATH"] = os.pathsep.join([
        str(root), str(root / "operator_kernel"),
        env.get("PYTHONPATH", ""),
    ])
    child = (
        "import sys, time\n"
        "from pathlib import Path\n"
        "gate = Path(sys.argv[2])\n"
        "while not gate.exists():\n"
        "    time.sleep(0.01)\n"
        "from operator_cli.project import ensure_registered\n"
        "rc, guid, created = ensure_registered(sys.argv[1])\n"
        "print(guid)\n"
        "raise SystemExit(rc)\n"
    )
    procs = [
        subprocess.Popen(
            [sys.executable, "-c", child, str(path), str(gate)],
            env=env, cwd=str(tmp_path),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        for path in (a, b)
    ]
    time.sleep(0.3)
    gate.write_text("go", encoding="utf-8")
    results = [p.communicate(timeout=30) for p in procs]
    codes = [p.returncode for p in procs]
    assert codes == [0, 0], results
    guids = [out.strip() for out, _err in results]
    assert len(set(guids)) == 2
    text = (home / "projects" / "catalog.csv").read_text(encoding="utf-8")
    assert guids[0] in text and guids[1] in text


def test_project_bootstraps_from_the_shared_home_helper():
    from operator_cli import home, project
    assert project._bootstrap is home._bootstrap
