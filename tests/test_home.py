"""The console scripts agree on one operator home, and find the kernel."""
from __future__ import annotations

import os
import sys
from pathlib import Path

from operator_cli import home

REPO = Path(__file__).resolve().parent.parent


def test_the_flag_beats_the_environment_and_is_exported(tmp_path, monkeypatch):
    monkeypatch.setenv("COPILOT_OPERATOR_HOME", str(tmp_path / "from-env"))
    flag = tmp_path / "from-flag"

    assert home._home(str(flag)) == flag
    assert home._settle_home(str(flag)) == flag
    assert os.environ["COPILOT_OPERATOR_HOME"] == str(flag)


def test_no_flag_uses_the_environment_then_the_default(tmp_path, monkeypatch):
    monkeypatch.setenv("COPILOT_OPERATOR_HOME", str(tmp_path / "from-env"))
    assert home._home(None) == tmp_path / "from-env"

    monkeypatch.delenv("COPILOT_OPERATOR_HOME")
    assert home._home(None) == Path.home() / ".operator"


def test_bootstrap_restores_the_kernel_directory_only(monkeypatch):
    kernel = str(REPO / "operator_kernel")
    monkeypatch.setattr(sys, "path", [p for p in sys.path if p != kernel])

    home._bootstrap()

    assert kernel in sys.path
