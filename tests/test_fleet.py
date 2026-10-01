"""operator-fleet still exposes the shared home helpers until that script goes."""
from __future__ import annotations

from operator_cli import fleet, home


def test_fleet_reexports_the_shared_home_helpers():
    assert fleet._home is home._home
    assert fleet._settle_home is home._settle_home


def test_fleet_bootstrap_restores_the_fleet_directory(monkeypatch):
    import sys
    from pathlib import Path
    fleet_dir = str(Path(fleet.__file__).resolve().parent.parent / "operator_fleet")
    monkeypatch.setattr(sys, "path", [p for p in sys.path if p != fleet_dir])
    fleet._bootstrap()
    assert fleet_dir in sys.path
