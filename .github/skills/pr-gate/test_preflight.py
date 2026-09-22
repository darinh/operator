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


def test_a_moved_ceiling_blocks_until_a_reason_is_given(monkeypatch):
    """Without a reason a raised budget is a side effect. With one it is a
    decision, and the reason lands in the output rather than in nobody's head."""
    def fake(*argv, cwd=None):
        return 0, "+MAX_KERNEL_CODE_LINES = 9999\n"

    monkeypatch.setattr(preflight, "run", fake)
    files = ["tests/test_kernel_boundary.py"]

    ok, detail = preflight.budgets_not_raised(files)
    assert ok is False
    assert "--budget-raised" in detail
    assert "9999" in detail

    ok, detail = preflight.budgets_not_raised(files, reason="extracted a seam first")
    assert ok is True
    assert "extracted a seam first" in detail


def test_the_guard_filter_does_not_leak_outside_tests(monkeypatch):
    """`a and b or c` without parentheses matched any path ending in the
    packaging test, anywhere in the tree."""
    monkeypatch.setattr(preflight, "run", lambda *a, **k: (0, ""))
    ok, detail = preflight.budgets_not_raised(
        ["vendor/somewhere/test_extension_packaging.py"])
    assert ok is True
    assert "no budget guard touched" in detail


def test_a_failed_guard_diff_does_not_silently_report_no_ceiling_moved(monkeypatch):
    monkeypatch.setattr(preflight, "run", lambda *a, **k: (128, "fatal"))
    ok, detail = preflight.budgets_not_raised(["tests/test_kernel_boundary.py"])
    assert ok is False
    assert "nothing is established" in detail


def test_changed_files_returns_none_when_git_fails(monkeypatch):
    """The earlier test handed None straight to the consumers, so reverting the
    producer to return [] still passed it. This exercises the producer."""
    monkeypatch.setattr(preflight, "run", lambda *a, **k: (128, "fatal: bad revision"))
    assert preflight.changed_files("no-such-base") is None


def test_changed_files_distinguishes_no_changes_from_no_answer(monkeypatch):
    monkeypatch.setattr(preflight, "run", lambda *a, **k: (0, ""))
    assert preflight.changed_files("main") == []


def test_a_drifted_pr_head_is_refused_even_when_local_ci_is_green(monkeypatch):
    """The earlier version of this test left the run list empty, so ok was
    False whether or not the PR-head check existed. Local HEAD now has a green
    test-workflow run, so only the head comparison can produce the refusal."""
    import json as _json

    def fake(*argv, cwd=None):
        if argv[:2] == ("git", "rev-parse") and "--abbrev-ref" in argv:
            return 0, "feat/pr-gate\n"
        if argv[:2] == ("git", "rev-parse"):
            return 0, "aaaaaaaaaaaa\n"
        if argv[:3] == ("gh", "pr", "view"):
            return 0, "bbbbbbbbbbbb\n"
        return 0, _json.dumps([
            {"headSha": "aaaaaaaaaaaa", "status": "completed",
             "conclusion": "success", "workflowName": preflight.WORKFLOW},
        ])

    monkeypatch.setattr(preflight, "run", fake)
    assert preflight.ci_is_green_on_head() == (
        True, f"{preflight.WORKFLOW!r} green on aaaaaaaa")
    ok, detail = preflight.ci_is_green_on_head("18")
    assert ok is False
    assert "not the one merging" in detail


def test_omitting_pr_cannot_report_gate_one_passed(monkeypatch, capsys):
    """Without --pr the PR head is never read, so a green run on local HEAD
    would otherwise certify a SHA that is not the one merging. Missing evidence
    must not become success, which is the shape of every hole found here."""
    import json as _json

    def fake(*argv, cwd=None):
        if argv[:2] == ("git", "rev-parse") and "--abbrev-ref" in argv:
            return 0, "feat/pr-gate\n"
        if argv[:2] == ("git", "rev-parse"):
            return 0, "aaaaaaaaaaaa\n"
        if argv[:2] == ("git", "status"):
            return 0, ""
        if argv[:2] == ("git", "diff"):
            return 0, ""
        return 0, _json.dumps([
            {"headSha": "aaaaaaaaaaaa", "status": "completed",
             "conclusion": "success", "workflowName": preflight.WORKFLOW},
        ])

    monkeypatch.setattr(preflight, "run", fake)
    monkeypatch.setattr(preflight, "suite_is_green", lambda: (True, "mocked green"))
    for argv in ([], ["--pr="], ["--pr", "  "]):
        code = preflight.main(argv)
        out = capsys.readouterr().out
        assert code == 1, argv
        assert "Gate 1 passed" not in out, argv
        assert "no --pr was given" in out, argv


def test_a_failed_git_status_is_not_a_clean_tree(monkeypatch):
    """Fifth instance of one pattern: a value meaning unknown treated as fine.
    git status exiting non-zero with no output looks exactly like a clean tree
    unless the exit code is read."""
    monkeypatch.setattr(preflight, "run", lambda *a, **k: (128, ""))
    ok, detail = preflight.tree_is_clean()
    assert ok is False
    assert "nothing is established" in detail


def test_an_unreadable_op_shim_is_not_a_bound_module(monkeypatch):
    monkeypatch.setattr(preflight.Path, "read_text",
                        lambda self, **kw: (_ for _ in ()).throw(OSError("nope")))
    ok, detail = preflight.kernel_modules_are_bound(
        ["operator_kernel/ledger_chain.py"])
    assert ok is False
    assert "nothing is established" in detail


def test_every_run_call_branches_on_its_own_status():
    """Structural guard, rewritten after a reviewer broke the first version.

    That one asked whether the name `code` appeared anywhere in the function,
    which accepted a deleted error branch, a second unchecked run() call beside
    a checked one, and a binding used only in a nested scope.

    This walks each run() call, takes the status it was unpacked into, and
    requires that exact name to appear in an `if` test in the same function.

    Known limits, stated rather than implied: it does not follow aliases of
    `run`, and it skips `main`, which orchestrates rather than probes. It is a
    lint, not a proof, and it does not replace per-function failure injection.
    """
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(preflight))
    offenders = []
    for func in ast.walk(tree):
        if not isinstance(func, ast.FunctionDef) or func.name in ("run", "main"):
            continue
        tested = {n.id
                  for node in ast.walk(func)
                  if isinstance(node, (ast.If, ast.Compare, ast.BoolOp))
                  for n in ast.walk(node) if isinstance(n, ast.Name)}
        for node in ast.walk(func):
            if not isinstance(node, ast.Assign):
                continue
            call = node.value
            if not (isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
                    and call.func.id == "run"):
                continue
            target = node.targets[0]
            if not isinstance(target, ast.Tuple) or not target.elts:
                offenders.append(f"{func.name} (status not unpacked)")
                continue
            status = target.elts[0]
            if not isinstance(status, ast.Name) or status.id not in tested:
                shown = getattr(status, "id", "?")
                offenders.append(f"{func.name} (status {shown!r} never tested)")
    assert not offenders, "run() calls whose status is not branched on: " + str(offenders)


def test_the_guard_rejects_the_reviewers_counterexamples():
    """The counterexamples that defeated the first guard, kept as fixtures so a
    future rewrite cannot quietly regress to a name-presence check."""
    import ast

    def offenders_in(source: str) -> list[str]:
        tree = ast.parse(source)
        found = []
        for func in ast.walk(tree):
            if not isinstance(func, ast.FunctionDef) or func.name in ("run", "main"):
                continue
            tested = {n.id
                      for node in ast.walk(func)
                      if isinstance(node, (ast.If, ast.Compare, ast.BoolOp))
                      for n in ast.walk(node) if isinstance(n, ast.Name)}
            for node in ast.walk(func):
                if not isinstance(node, ast.Assign):
                    continue
                call = node.value
                if not (isinstance(call, ast.Call)
                        and isinstance(call.func, ast.Name)
                        and call.func.id == "run"):
                    continue
                target = node.targets[0]
                if not isinstance(target, ast.Tuple) or not target.elts:
                    found.append(func.name)
                    continue
                status = target.elts[0]
                if not isinstance(status, ast.Name) or status.id not in tested:
                    found.append(func.name)
        return found

    second_call_unchecked = '''
def unchecked_second():
    code, _ = run("git", "rev-parse", "HEAD")
    if code:
        return False, "cannot resolve HEAD"
    _second, out = run("git", "status", "--porcelain")
    return not out.strip(), "clean"
'''
    branch_deleted = '''
def branch_deleted():
    code, out = run("git", "status", "--porcelain")
    return not out.strip(), "clean"
'''
    honest = '''
def honest():
    code, out = run("git", "status", "--porcelain")
    if code != 0:
        return False, "unknown"
    return not out.strip(), "clean"
'''
    assert offenders_in(second_call_unchecked) == ["unchecked_second"]
    assert offenders_in(branch_deleted) == ["branch_deleted"]
    assert offenders_in(honest) == []


def test_a_quoted_mention_does_not_register_a_module(monkeypatch):
    """A substring search over the shim accepts a name in a comment. tests/op.py
    really does mention "is_repo_module", which is a function and not a module,
    so this was not hypothetical."""
    bound, problem = preflight._module_names()
    assert problem is None
    assert "is_repo_module" not in bound
    assert "evidence" in bound

    real = (preflight.REPO / "tests" / "op.py").read_text(encoding="utf-8")
    salted = real + '\n# "new_guard" has not been registered yet\n'
    monkeypatch.setattr(preflight.Path, "read_text",
                        lambda self, **kw: salted)
    ok, detail = preflight.kernel_modules_are_bound(
        ["operator_kernel/new_guard.py"])
    assert ok is False
    assert "new_guard" in detail


def test_an_unparseable_shim_establishes_nothing(monkeypatch):
    monkeypatch.setattr(preflight.Path, "read_text",
                        lambda self, **kw: "def broken(:\n")
    ok, detail = preflight.kernel_modules_are_bound(
        ["operator_kernel/ledger_chain.py"])
    assert ok is False
    assert "nothing is established" in detail
