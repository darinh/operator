"""Disposable operator home, real git repo with a bare remote, child spawn."""
from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)


@dataclass
class World:
    root: Path
    home: Path
    repo: Path
    remote: Path
    bin: Path

    def close(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)


def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def make_world(parent: Path) -> World:
    root = Path(parent) / "world"
    root.mkdir(parents=True)
    home = root / "home"
    home.mkdir()
    repo = root / "repo"
    repo.mkdir()
    remote = root / "remote.git"
    bin_dir = root / "bin"
    bin_dir.mkdir()
    _write_dummy_copilot(bin_dir)
    _init_repo(repo, remote)
    return World(root=root, home=home, repo=repo, remote=remote, bin=bin_dir)


def git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    proc = subprocess.run(
        ("git",) + args,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=_git_env(),
        timeout=60,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"git {' '.join(args)} failed ({proc.returncode}): "
            f"{proc.stderr or proc.stdout}"
        )
    return proc


def refs(cwd: Path) -> str:
    return git(
        cwd, "for-each-ref", "--format=%(objectname) %(refname)",
        "refs/heads", "refs/tags", "refs/stash", "refs/remotes",
    ).stdout


def spawn(world: World, argv: list[str]) -> subprocess.Popen:
    env = os.environ.copy()
    env["COPILOT_OPERATOR_HOME"] = str(world.home)
    env["PATH"] = str(world.bin) + os.pathsep + env.get("PATH", "")
    root = repo_root()
    env["PYTHONPATH"] = os.pathsep.join((
        str(root / "operator_kernel"),
        str(root / "operator_fleet"),
        str(root),
    ))
    kwargs: dict = {}
    if os.name == "nt":
        kwargs["creationflags"] = _CREATE_NO_WINDOW
    out = (world.home / "child.stdout").open("w", encoding="utf-8", errors="replace")
    err = (world.home / "child.stderr").open("w", encoding="utf-8", errors="replace")
    try:
        proc = subprocess.Popen(
            argv,
            cwd=str(world.repo),
            env=env,
            stdout=out,
            stderr=err,
            **kwargs,
        )
    except Exception:
        out.close()
        err.close()
        raise
    proc._bench_out = out
    proc._bench_err = err
    return proc


def _close_stdio(proc: subprocess.Popen) -> None:
    for handle in (getattr(proc, "_bench_out", None),
                   getattr(proc, "_bench_err", None)):
        if handle is not None:
            try:
                handle.close()
            except OSError:
                pass
    proc._bench_out = None
    proc._bench_err = None


def kill(proc: subprocess.Popen, timeout: float = 10.0) -> None:
    if proc.poll() is None:
        proc.kill()
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.terminate()
            proc.wait(timeout=timeout)
    _close_stdio(proc)


def wait(proc: subprocess.Popen, timeout: float) -> int:
    try:
        rc = int(proc.wait(timeout=timeout))
        _close_stdio(proc)
        return rc
    except subprocess.TimeoutExpired:
        kill(proc)
        raise


def _git_env() -> dict[str, str]:
    env = os.environ.copy()
    env.pop("GIT_DIR", None)
    env.pop("GIT_WORK_TREE", None)
    env["GIT_TERMINAL_PROMPT"] = "0"
    return env


def _write_dummy_copilot(bin_dir: Path) -> None:
    if os.name == "nt":
        (bin_dir / "copilot.cmd").write_text(
            "@echo off\r\nexit /b 0\r\n", encoding="ascii")
        return
    path = bin_dir / "copilot"
    path.write_text("#!/bin/sh\nexit 0\n", encoding="ascii")
    path.chmod(0o755)


def _init_repo(repo: Path, remote: Path) -> None:
    git(repo, "init", "-b", "main")
    git(repo, "config", "user.name", "bench (agent)")
    git(repo, "config", "user.email", "bench@example.invalid")
    git(repo, "config", "commit.gpgsign", "false")
    (repo / "README").write_text("bench repo\n", encoding="ascii")
    git(repo, "add", "-A")
    git(repo, "commit", "-m", "base")
    git(repo, "init", "--bare", "-b", "main", str(remote))
    git(repo, "remote", "add", "origin", str(remote))
    git(repo, "push", "-q", "-u", "origin", "main")
