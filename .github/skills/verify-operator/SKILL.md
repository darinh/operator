---
name: verify-operator
description: Drive the operator CLI against a disposable operator home and capture proof. Use when changing the kernel, session handoff, or the supervisor and you need evidence the real commands still behave, not just that pytest is green.
---

# Verify operator

`operator` starts a supervised Copilot CLI session and lets that session hand off to the next one. It installs one console script. This skill drives it.

There is no server and no UI. The commands are short-lived, they read and write a state directory, and **that directory is the thing under test**. So "launch" here means *build a disposable operator home*, and every drive runs against it.

`python -m pytest -q` is the unit suite and is not a substitute for this. The suite substitutes the multiplexer, the home and the catalog; this skill runs the installed console script against a real home, through real entry-point discovery, and reads what actually landed on disk.

## Isolation guarantee

This skill never touches your real `~/.operator`, and that is checked rather than claimed. Every command sets `COPILOT_OPERATOR_HOME` **before** spawning a fresh process, so the kernel's import-time `config.OPERATOR_HOME` resolves to the run's home in the child. `doctor` asserts the home is not the real one.

The catalog it writes lives inside the disposable home and is deleted by `down`.

> **Why the ordering matters.** `config.py` evaluates `OPERATOR_HOME = operator_home()` at import time, and `LOG_FILE` / `RESTART_DIR` derive from it. In-process code that sets the environment variable *after* importing the kernel therefore addresses the real `~/.operator`. This skill escapes that by setting the variable before the child process starts; the unit suite escapes it through the `_no_real_operator_home` guard in `tests/conftest.py`.

## Launch

```
python .github/skills/verify-operator/control_operator.py up --run-id <id>
```

It prints the run, home and artifacts paths and the project guid. **Keep the `run` path**; every later command takes it as `--run`. It creates:

```
.verify-operator/<run-id>/
    home/        the isolated operator home   (removed by `down`)
    artifacts/   transcripts and snapshots    (survives `down`)
    run.json     repo path, project guid, both directories
```

`up` registers this checkout in the run's own `projects/catalog.csv`. That registration is what makes the directory a project; without it `operator handoff` has nowhere to write.

`.verify-operator/` is gitignored.

## Doctor

```
python .github/skills/verify-operator/control_operator.py doctor --run <run>
```

Read-only. Exit `0` and `doctor: healthy` mean the instance is worth driving. It checks the run exists, `operator` resolves on `PATH`, **the installed `operator_kernel` is this checkout**, the kernel version is readable, the home exists, the home is not `~/.operator`, and the catalog registers the repo.

If the console script is missing: `pip install -e .`

## Drive

Every verb takes `--run <run>`. `--label <name>` names the transcript entry.

| Intent | Command |
| --- | --- |
| Hand off | Not from this harness. `operator handoff` accepts a caller only when that process is a descendant of the copilot the runner recorded. This helper is a separate process, so the command exits 2 and writes nothing. The unit suite covers the protocol. |

Everything after `--` is passed to `operator` untouched. The helper runs from the registered checkout unless `--cwd` says otherwise.

## Evidence

```
python .github/skills/verify-operator/control_operator.py evidence --run <run> --label after-handoff
```

Every `operator` call appends the command, exit code, stdout and stderr to `artifacts/transcript.md`. `evidence` adds a labelled snapshot of `operator.log`, the catalog, every handoff under `projects/*/handoff/*.md` and everything under `restart/*`, plus a `MANIFEST.txt` naming sizes.

The last two are one pair. The handoff file is what the next session reads and the restart marker is what the supervisor polls, so a snapshot showing only one of them cannot tell "handed off" from "ended without leaving anything".

## Cleanup

```
python .github/skills/verify-operator/control_operator.py down --run <run>
```

Removes `home/` and nothing else. Idempotent. **It never deletes `artifacts/`**.

## Helpers

| Verb | Purpose |
| --- | --- |
| `up` | create the disposable home and register the repo |
| `doctor` | read-only health check |
| `operator` | run `operator` from the registered checkout (`--cwd` to override) |
| `evidence` | snapshot home state under a label |
| `down` | remove the instance, keep the artifacts |

The harness has its own tests, because a harness bug produces a *false* proof rather than a failed one:

```
python -m pytest .github/skills/verify-operator/test_control_operator.py -q
```
