"""Mechanical half of the PR gate. Judgement lives in SKILL.md."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent.parent
SOURCE_DIRS = ("operator_kernel", "operator_fleet", "operator_cli",
               "operator_extensions", "operator_memory", "operator_bench")

#: The workflow that actually runs the tests, from .github/workflows/tests.yml.
#: Matching on any workflow would let an unrelated green run certify the tests.
WORKFLOW = "tests"


def run(*argv: str, cwd: Path | None = None) -> tuple[int, str]:
    try:
        done = subprocess.run(argv, cwd=str(cwd or REPO), capture_output=True,
                              text=True, encoding="utf-8", errors="replace")
    except (FileNotFoundError, OSError) as exc:
        return 127, f"could not run {argv[0]}: {exc}"
    return done.returncode, (done.stdout or "") + (done.stderr or "")


def changed_files(base: str) -> list[str] | None:
    """Paths changed against `base`, or None when git could not tell us.

    None is not an empty list. An empty list means nothing changed; None means
    the question went unanswered, and a gate that passes when it cannot see the
    diff is worse than no gate.
    """
    code, out = run("git", "diff", "--name-only", f"{base}...HEAD")
    if code != 0:
        return None
    return [line.strip() for line in out.splitlines() if line.strip()]


def tree_is_clean() -> tuple[bool, str]:
    _code, out = run("git", "status", "--porcelain")
    dirty = [line for line in out.splitlines() if line.strip()]
    return (not dirty), ("clean" if not dirty else f"{len(dirty)} uncommitted path(s)")


def sources_have_tests(files: list[str] | None) -> tuple[bool, str]:
    """Every changed source must have its test file changed in the same diff.

    Existence is too weak. Adding two hundred lines to a module whose test file
    was written a year ago satisfies "a test file exists" without a single new
    assertion, which is the `test-enforcer` hook's rule at commit time and has
    to be the rule here too.
    """
    if files is None:
        return False, "could not read the diff, so nothing is established"
    changed = set(files)
    unguarded = []
    for path in files:
        parts = Path(path).parts
        if not parts or parts[0] not in SOURCE_DIRS or not path.endswith(".py"):
            continue
        stem = Path(path).stem
        if stem == "__init__":
            continue
        test = f"tests/test_{stem}.py"
        if test not in changed:
            unguarded.append(f"{path} (wanted {test} in this diff)")
    return (not unguarded), ("each changed source changed its test too"
                             if not unguarded else "; ".join(unguarded))


def budgets_not_raised(files: list[str] | None, base: str = "main",
                       reason: str | None = None) -> tuple[bool, str]:
    """A raised ceiling has to be a stated decision, so state it.

    `reason` is how you state it. Without one a moved `MAX_*` blocks the gate;
    with one it passes and the reason is printed, which is the difference
    between a decision and a side effect.
    """
    if files is None:
        return False, "could not read the diff, so nothing is established"
    guards = [p for p in files
              if p.startswith("tests/")
              and ("boundary" in p or p.endswith("test_extension_packaging.py"))]
    if not guards:
        return True, "no budget guard touched"
    raised = []
    for path in guards:
        code, out = run("git", "diff", "-U0", f"{base}...HEAD", "--", path)
        if code != 0:
            return False, f"could not diff {path}, so nothing is established"
        for line in out.splitlines():
            if line.startswith("+") and "MAX_" in line and "=" in line:
                raised.append(f"{path}: {line[1:].strip()}")
    if not raised:
        return True, "no ceiling moved"
    if reason:
        return True, f"raised deliberately ({reason}): " + "; ".join(raised)
    return False, ("pass --budget-raised with a reason: " + "; ".join(raised))


def kernel_modules_are_bound(files: list[str] | None) -> tuple[bool, str]:
    if files is None:
        return False, "could not read the diff, so nothing is established"
    new = [Path(p).stem for p in files
           if p.startswith("operator_kernel/") and p.endswith(".py")
           and Path(p).stem != "__init__"]
    if not new:
        return True, "no kernel modules touched"
    shim = (REPO / "tests" / "op.py").read_text(encoding="utf-8")
    absent = [name for name in new if f'"{name}"' not in shim]
    return (not absent), ("all bound in tests/op.py" if not absent
                          else "absent from _MODULE_NAMES: " + ", ".join(absent))


def ci_is_green_on_head(pr: str | None = None) -> tuple[bool, str]:
    """The named workflow concluded success on the SHA this PR will merge.

    Three things this deliberately does not accept. Any workflow succeeding on
    the SHA, because an unrelated one proves nothing about the tests. A run on
    an earlier SHA, because a rebase makes a new one. And local HEAD when it
    has drifted from the PR head, because the PR is what merges.
    """
    code, head = run("git", "rev-parse", "HEAD")
    if code != 0:
        return False, "cannot resolve HEAD"
    sha = head.strip()

    if pr:
        code, out = run("gh", "pr", "view", pr, "--json", "headRefOid",
                        "--jq", ".headRefOid")
        if code != 0:
            return False, f"cannot read PR {pr} head: {out.strip()[:120]}"
        remote = out.strip()
        if remote and remote != sha:
            return False, (f"local HEAD {sha[:8]} is not PR {pr} head "
                           f"{remote[:8]}, so the checked SHA is not the one merging")

    code, out = run("gh", "run", "list", "--limit", "60", "--json",
                    "headSha,status,conclusion,workflowName")
    if code != 0:
        return False, out.strip()[:160]
    try:
        runs = json.loads(out)
    except json.JSONDecodeError:
        return False, "could not parse gh output"
    mine = [r for r in runs if r.get("headSha") == sha
            and r.get("workflowName") == WORKFLOW]
    if not mine:
        return False, (f"no {WORKFLOW!r} run for {sha[:8]}; --auto would merge "
                       f"unchecked, and an unrelated workflow does not count")
    bad = [r for r in mine if r.get("status") != "completed"
           or r.get("conclusion") != "success"]
    if bad:
        return False, f"{WORKFLOW!r} on {sha[:8]} is {bad[0].get('conclusion') or bad[0].get('status')}"
    return True, f"{WORKFLOW!r} green on {sha[:8]}"


def suite_is_green() -> tuple[bool, str]:
    code, out = run(sys.executable, "-m", "pytest", "-q")
    tail = [line for line in out.splitlines() if line.strip()]
    return code == 0, (tail[-1] if tail else "no output")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="preflight",
                                     description="Mechanical PR gate checks.")
    parser.add_argument("--base", default="main")
    parser.add_argument("--pr", default=None,
                        help="PR number, so the checked SHA is bound to its head")
    parser.add_argument("--skip-tests", action="store_true")
    parser.add_argument("--budget-raised", default=None, metavar="REASON",
                        help="acknowledge a moved MAX_* ceiling, with why")
    args = parser.parse_args(argv)

    files = changed_files(args.base)
    checks = [
        ("working tree clean", tree_is_clean()),
        ("changed sources changed their tests", sources_have_tests(files)),
        ("kernel modules bound in op shim", kernel_modules_are_bound(files)),
        ("no budget ceiling moved silently",
         budgets_not_raised(files, args.base, args.budget_raised)),
        ("test workflow green on the merging SHA", ci_is_green_on_head(args.pr)),
    ]
    if args.skip_tests:
        checks.append(("suite green", (False, "skipped, so the gate is incomplete")))
    else:
        checks.append(("suite green", suite_is_green()))

    failed = 0
    for name, (ok, detail) in checks:
        if not ok:
            failed += 1
        print(f"[{'PASS' if ok else 'FAIL'}] {name}: {detail}")

    print()
    if failed:
        print(f"{failed} check(s) failed. Gate 1 is not passed.")
    else:
        print("Gate 1 passed. Now gates 2 and 3 in SKILL.md, which need judgement.")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
