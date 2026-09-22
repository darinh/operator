"""The gate's own checks, exercised against built inputs rather than a live PR."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import preflight  # noqa: E402


def test_an_unreadable_diff_fails_every_check_that_depends_on_it():
    """changed_files returns None when git could not answer. A gate that passes
    because it could not see the diff is worse than no gate."""
    for check in (preflight.sources_have_tests,
                  preflight.kernel_modules_are_bound,
                  preflight.budgets_not_raised):
        ok, detail = check(None)
        assert ok is False, check.__name__
        assert "could not read the diff" in detail


def test_a_source_changed_without_its_test_fails():
    ok, detail = preflight.sources_have_tests(["operator_kernel/evidence.py"])
    assert ok is False
    assert "tests/test_evidence.py" in detail


def test_a_source_changed_with_its_test_passes():
    ok, _detail = preflight.sources_have_tests(
        ["operator_kernel/evidence.py", "tests/test_evidence.py"])
    assert ok is True


def test_an_existing_test_file_is_not_enough_on_its_own():
    """tests/test_supervisor.py exists in this repo. Editing supervisor.py
    without touching it must still fail, or the check rewards a test written a
    year ago for code added today."""
    assert (preflight.REPO / "tests" / "test_supervisor.py").exists()
    ok, _detail = preflight.sources_have_tests(["operator_kernel/supervisor.py"])
    assert ok is False


def test_non_source_paths_are_ignored():
    ok, _detail = preflight.sources_have_tests(
        ["docs/ledger.md", ".audit/trail.tsv", "README.md"])
    assert ok is True


def test_dunder_init_needs_no_test_file():
    ok, _detail = preflight.sources_have_tests(["operator_kernel/__init__.py"])
    assert ok is True


def test_a_new_kernel_module_absent_from_the_op_shim_fails():
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


def test_a_diff_touching_no_budget_guard_passes():
    ok, detail = preflight.budgets_not_raised(["operator_kernel/evidence.py"])
    assert ok is True
    assert "no budget guard touched" in detail


def test_skipping_the_suite_cannot_report_gate_one_passed(capsys):
    """--skip-tests is for iterating, not for passing. It must not be a way to
    print a pass without running anything."""
    code = preflight.main(["--skip-tests"])
    out = capsys.readouterr().out
    assert code == 1
    assert "Gate 1 passed" not in out
    assert "skipped, so the gate is incomplete" in out


def test_a_missing_executable_is_reported_not_raised():
    """subprocess.run raises FileNotFoundError when the binary is absent, so
    the nonzero-exit handler never sees it. The PR description claimed this
    degraded gracefully and a reviewer proved it crashed."""
    code, detail = preflight.run("definitely-not-an-executable-xyz", "--help")
    assert code == 127
    assert "could not run" in detail


def test_an_unrelated_green_workflow_does_not_certify_the_tests(monkeypatch):
    """A successful run of some other workflow on the same SHA proves nothing
    about the test suite."""
    import json as _json

    def fake(*argv, cwd=None):
        if argv[:2] == ("git", "rev-parse"):
            return 0, "abc123def456\n"
        return 0, _json.dumps([
            {"headSha": "abc123def456", "status": "completed",
             "conclusion": "success", "workflowName": "codeql"},
        ])

    monkeypatch.setattr(preflight, "run", fake)
    ok, detail = preflight.ci_is_green_on_head()
    assert ok is False
    assert "unrelated workflow does not count" in detail


def test_the_test_workflow_green_on_the_sha_passes(monkeypatch):
    import json as _json

    def fake(*argv, cwd=None):
        if argv[:2] == ("git", "rev-parse"):
            return 0, "abc123def456\n"
        return 0, _json.dumps([
            {"headSha": "abc123def456", "status": "completed",
             "conclusion": "success", "workflowName": preflight.WORKFLOW},
        ])

    monkeypatch.setattr(preflight, "run", fake)
    ok, _detail = preflight.ci_is_green_on_head()
    assert ok is True


def test_a_local_head_that_drifted_from_the_pr_head_fails(monkeypatch):
    """The PR head is what merges. Checking local HEAD alone certifies a SHA
    that may no longer be the one going in."""
    def fake(*argv, cwd=None):
        if argv[:2] == ("git", "rev-parse"):
            return 0, "aaaaaaaaaaaa\n"
        if argv[:3] == ("gh", "pr", "view"):
            return 0, "bbbbbbbbbbbb\n"
        return 0, "[]"

    monkeypatch.setattr(preflight, "run", fake)
    ok, detail = preflight.ci_is_green_on_head("18")
    assert ok is False
    assert "not the one merging" in detail
