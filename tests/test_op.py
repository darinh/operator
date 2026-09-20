"""The op shim bind list has to name every kernel module the suite patches."""
from __future__ import annotations

import op


def test_ledger_chain_is_bound_by_the_shim():
    """A new kernel module is invisible to monkeypatching unless it is listed.

    That already happened in this repository. ledger_chain is the next one.
    """
    assert "ledger_chain" in op._MODULE_NAMES
    assert op.is_repo_module(op.ledger_chain) is True
    assert hasattr(op.ledger_chain, "verify_records")
