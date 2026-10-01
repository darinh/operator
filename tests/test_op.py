"""The op shim bind list has to name every kernel module the suite patches."""
from __future__ import annotations

import op


def test_process_tree_is_bound_by_the_shim():
    assert "process_tree" in op._MODULE_NAMES
    assert op.is_repo_module(op.process_tree) is True
    assert hasattr(op.process_tree, "ancestry")


def test_argtail_is_bound_by_the_shim():
    assert "argtail" in op._MODULE_NAMES
    assert op.is_repo_module(op.argtail) is True
    assert hasattr(op.argtail, "before_terminator")
