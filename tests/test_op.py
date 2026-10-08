"""The op shim bind list has to name every kernel module the suite patches."""
from __future__ import annotations

import op


def test_argtail_is_bound_by_the_shim():
    assert "argtail" in op._MODULE_NAMES
    assert op.is_repo_module(op.argtail) is True
    assert hasattr(op.argtail, "before_terminator")


def test_lineage_is_bound_by_the_shim():
    assert "lineage" in op._MODULE_NAMES
    assert op.is_repo_module(op.lineage) is True
    assert hasattr(op.lineage, "subtree")


def test_the_shim_names_every_kernel_module():
    stems = {path.stem for path in op.KERNEL.glob("*.py") if path.stem != "__init__"}
    assert set(op._MODULE_NAMES) == stems
