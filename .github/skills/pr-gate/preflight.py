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


def run(*argv: str, cwd: Path | None = None) -> tuple[int, str]:
    done = subprocess.run(argv, cwd=str(cwd or REPO), capture_output=True,
                          text=True, encoding="utf-8", errors="replace")
    return done.returncode, (done.stdout or "") + (done.stderr or "")


def changed_files(base: str) -> list[str]:
    code, out = run("git", "diff", "--name-only", f"{base}...HEAD")
    if code != 0:
        return []
    return [line.strip() for line in out.splitlines() if line.strip()]


def tree_is_clean() -> tuple[bool, str]:
    _code, out = run("git", "status", "--porcelain")
    dirty = [line for line in out.splitlines() if line.strip()]
    return (not dirty), ("clean" if not dirty else f"{len(dirty)} uncommitted path(s)")


def sources_have_tests(files: list[str]) -> tuple[bool, str]:
    missing = []
    for path in files:
        parts = Path(path).parts
        if not parts or parts[0] not in SOURCE_DIRS or not path.endswith(".py"):
            continue
        stem = Path(path).stem
        if stem == "__init__":
            continue
        if not (REPO / "tests" / f"test_{stem}.py").exists():
            missing.append(path)
    return (not missing), ("every changed source has one" if not missing
                           else "no test file for " + ", ".join(missing))


def kernel_modules_are_bound(files: list[str]) -> tuple[bool, str]:
    new = [Path(p).stem for p in files
           if p.startswith("operator_kernel/") and p.endswith(".py")
           and Path(p).stem != "__init__"]
    if not new:
        return True, "no kernel modules touched"
    shim = (REPO / "tests" / "op.py").read_text(encoding="utf-8")
    absent = [name for name in new if f'"{name}"' not in shim]
    return (not absent), ("all bound in tests/op.py" if not absent
                          else "absent from _MODULE_NAMES: " + ", ".join(absent))


def ci_is_green_on_head() -> tuple[bool, str]:
    code, head = run("git", "rev-parse", "HEAD")
    if code != 0:
        return False, "cannot resolve HEAD"
    sha = head.strip()
    code, out = run("gh", "run", "list", "--limit", "25", "--json",
                    "headSha,status,conclusion,databaseId")
    if code != 0:
        return False, "gh unavailable, check CI by hand"
    try:
        runs = json.loads(out)
    except json.JSONDecodeError:
        return False, "could not parse gh output"
    mine = [r for r in runs if r.get("headSha") == sha]
    if not mine:
        return False, f"no workflow run exists for {sha[:8]}, so --auto would merge unchecked"
    bad = [r for r in mine if r.get("status") != "completed"
           or r.get("conclusion") != "success"]
    if bad:
        return False, f"{len(bad)} run(s) on {sha[:8]} not successful"
    return True, f"{len(mine)} run(s) green on {sha[:8]}"


def suite_is_green() -> tuple[bool, str]:
    code, out = run(sys.executable, "-m", "pytest", "-q")
    tail = [line for line in out.splitlines() if line.strip()]
    return code == 0, (tail[-1] if tail else "no output")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="preflight",
                                     description="Mechanical PR gate checks.")
    parser.add_argument("--base", default="main")
    parser.add_argument("--skip-tests", action="store_true")
    args = parser.parse_args(argv)

    files = changed_files(args.base)
    checks = [
        ("working tree clean", tree_is_clean()),
        ("changed sources have tests", sources_have_tests(files)),
        ("kernel modules bound in op shim", kernel_modules_are_bound(files)),
        ("CI green on exact head SHA", ci_is_green_on_head()),
    ]
    if not args.skip_tests:
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
