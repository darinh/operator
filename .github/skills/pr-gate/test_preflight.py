"""The gate's own checks, exercised against built inputs rather than a live PR."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import preflight  # noqa: E402


def test_a_changed_source_without_a_test_file_fails():
    ok, detail = preflight.sources_have_tests(
        ["operator_kernel/definitely_not_a_real_module.py"])
    assert ok is False
    assert "definitely_not_a_real_module" in detail


def test_a_changed_source_with_a_test_file_passes():
    ok, _detail = preflight.sources_have_tests(["operator_kernel/evidence.py"])
    assert ok is True


def test_non_source_paths_are_ignored():
    ok, _detail = preflight.sources_have_tests(
        ["docs/ledger.md", ".audit/trail.tsv", "README.md"])
    assert ok is True


def test_dunder_init_needs_no_test_file():
    ok, _detail = preflight.sources_have_tests(["operator_kernel/__init__.py"])
    assert ok is True


def test_a_new_kernel_module_absent_from_the_op_shim_fails():
    """tests/op.py's _MODULE_NAMES is a hand-written list, and a hand-written
    list is the thing a new name is absent from."""
    ok, detail = preflight.kernel_modules_are_bound(
        ["operator_kernel/definitely_not_a_real_module.py"])
    assert ok is False
    assert "_MODULE_NAMES" in detail


def test_a_kernel_module_already_in_the_shim_passes():
    ok, _detail = preflight.kernel_modules_are_bound(
        ["operator_kernel/ledger_chain.py"])
    assert ok is True


def test_touching_no_kernel_module_is_not_a_failure():
    ok, detail = preflight.kernel_modules_are_bound(["operator_bench/score.py"])
    assert ok is True
    assert "no kernel modules" in detail
