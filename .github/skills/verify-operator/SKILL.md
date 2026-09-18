---
name: verify-operator
description: Drive the operator CLIs (operator-fleet, operator-seat) against a disposable operator home and capture proof. Use when changing the kernel, fleet host, proposal queue, seat journal or an extension and you need evidence the real commands still behave, not just that pytest is green.
---

# Verify operator

`operator` is a supervision kernel for fleets of autonomous coding-agent sessions.
Its user-facing surface is two console scripts installed from this repo:

- **`operator-fleet`** — `run` polls the ledger and wakes enabled extensions;
  `proposals` shows and drains the NEEDS-HUMAN queue.
- **`operator-seat`** — `remember` / `recall` / `forget`, a seat's cross-session
  journal for one project.

There is no server, no UI and no long-lived process to attach to. Both commands
are short-lived, they read and write a state directory, and **that directory is
the thing under test**. So "launch" here means *build a disposable operator home*,
and every drive runs against it.

**Everything in this skill works on a core install with no extensions enabled.**
Extensions are an optional layer, documented separately in
[`features/extensions.md`](./features/extensions.md). Nothing in the core path
below names a particular extension, and the helper has no knowledge of any.

`python -m pytest -q` is the unit suite and is not a substitute for this. The
suite substitutes the multiplexer, the home and the catalog; this skill runs the
installed console scripts against a real home, through real entry-point
discovery, and reads what actually landed on disk.

## Isolation guarantee

This skill never touches your real `~/.operator`, and that is checked rather than
claimed. Every command sets `COPILOT_OPERATOR_HOME` **before** spawning a fresh
process, so the kernel's import-time `config.OPERATOR_HOME` resolves to the run's
home in the child. `doctor` asserts the home is not the real one.

Verified: a full run of every verb left the real `~/.operator/operator.log`
byte-for-byte unchanged, while the run's own home grew its own log.

Two things this skill deliberately does **not** do:

- It never writes `~/.operator/extensions.json`. `enable` writes only into the
  run's disposable home, so enabling something here cannot switch it on for
  every repo on the machine.
- It registers no project globally. The catalog it writes lives inside the
  disposable home and is deleted by `down`.

One thing it cannot isolate, because it is inherent to the checkout: `pip install
-e .` registers this repo's extension entry points for the whole Python
environment. Registration is not activation — they stay inert until an
`extensions.json` enables them — but `operator-fleet` will list them at discovery.

> **Known defect, unrelated to this skill.** `config.py` evaluates
> `OPERATOR_HOME = operator_home()` at import time, and `LOG_FILE` / `RESTART_DIR`
> derive from it. In-process code that sets the environment variable *after*
> importing the kernel therefore writes to the real `~/.operator` — which is why
> `python -m pytest -q` appends to your live `operator.log`. This skill escapes it
> only by setting the variable before the child process starts.

## Launch

One helper owns the whole lifecycle:

```
python .github/skills/verify-operator/control_operator.py up
```

It prints the run, home and artifacts paths, the project guid, and confirms that
no extensions are enabled. **Keep the `run` path**; every later command takes it
as `--run`. It creates:

```
.verify-operator/<run-id>/
    home/        the isolated operator home   (removed by `down`)
    artifacts/   transcripts and snapshots    (survives `down`)
    run.json     repo path, project guid, both directories
```

`up` registers this checkout in the run's own `projects/catalog.csv` under a fresh
guid. That registration is what makes the directory a "project"; without it
`operator-seat` writes nothing.

Readiness is not a port or a log line — it is `doctor` passing.
`.verify-operator/` is gitignored.

## Doctor

```
python .github/skills/verify-operator/control_operator.py doctor --run <run>
```

Read-only. Exit `0` and `doctor: healthy` mean the instance is worth driving. It
checks the run exists, both console scripts resolve on `PATH`, **the installed
`operator_kernel` is this checkout** (an editable install pointing elsewhere would
prove something about another tree), the kernel version is readable, the home
exists, the home is not `~/.operator`, and the catalog registers the repo. It
then reports which extensions are enabled — `none (core only)` is a normal and
expected answer, not a failure.

Run it first whenever anything looks off. After `down` it reports `NOT healthy`
with exit `1`.

If the console scripts are missing: `pip install -e .`

## Drive

Every verb takes `--run <run>`. `--label <name>` names the transcript entry.
Nothing in this table names an extension.

| Intent | Command |
| --- | --- |
| Write a memory | `control_operator.py seat --run <run> --label remember -- --instance verify-seat --session 1 remember --kind gotcha "text"` |
| Read memories | `control_operator.py seat --run <run> --label recall -- --instance verify-seat recall` |
| Supersede one | `control_operator.py seat --run <run> --label forget -- --instance verify-seat forget <id>` |
| Drive from outside the project | `control_operator.py seat --run <run> --cwd <dir> -- --instance verify-seat recall` |
| Run the fleet host | `control_operator.py fleet --run <run> --label round -- run --rounds 1 --interval 0.1` |
| Show the queue | `control_operator.py fleet --run <run> --label queue -- proposals` |
| Drain the queue | `control_operator.py fleet --run <run> --label drain -- proposals --drain` |
| Seed a proposal (fixture) | `control_operator.py seed-queue --run <run> --extension verify-fixture` |
| Seed an orphaned batch (fixture) | `control_operator.py seed-queue --run <run> --extension crashed --abandoned` |
| Seed a ledger record (fixture) | `control_operator.py seed-ledger --run <run> --record '{"event":"...","instance":"..."}'` |
| Enable any extension | `control_operator.py enable --run <run> --extension <name> --setting key=value` |

Everything after `--` is passed to the console script untouched, so the flags are
the ones a user types. Two things the helper handles that a hand-rolled call gets
wrong: `operator-seat` has **no `--home` flag** and resolves the home only from
`COPILOT_OPERATOR_HOME`, and seat commands must run with the **registered checkout
as the working directory**, because the journal is resolved from `Path.cwd()`.

Drive by stable handles, not by output position: ledger records key on `event` and
`instance`, proposals on the extension name, journal entries on the 8-character id
`remember` prints.

**The ledger tail advances on every round and never redelivers.**
`fleet-tail.json` holds a byte offset into `trace.jsonl`. The cursor is rewound
only when *nobody was asked*, and an installed extension is asked whether or not
it is enabled — so on this checkout, where three are always installed, a round
with nothing enabled spends the batch exactly like an active one. Seed again
after enabling anything. This is the most common way to spend a run proving
nothing.

## Evidence

```
python .github/skills/verify-operator/control_operator.py evidence --run <run> --label before-drain
```

Every `fleet` and `seat` call already appends the command, exit code, stdout and
stderr to `artifacts/transcript.md`. `evidence` adds a labelled snapshot of the
home's state files — `trace.jsonl`, `proposals.jsonl`, `proposals.handled.jsonl`,
`fleet-failures.jsonl`, `fleet-tail.json`, `extensions.json`, `operator.log`, any
`extensions/*.json`, the catalog and every seat journal — plus a `MANIFEST.txt`
naming sizes.

Proof standards:

- **Drive the real console script.** Calling `journal.remember()` in-process skips
  argument parsing, the catalog lookup and the exit code, which is most of what
  can break.
- **Capture the action and the resulting state.** A `remember` that printed an id
  is not proof; the entry in `projects/<guid>/journal/<seat>.jsonl` is.
- **Check side effects, not just stdout.** A drain that prints proposals must also
  leave `proposals.jsonl` gone and `proposals.handled.jsonl` grown by the same
  number of bytes.
- **Take two snapshots around anything that mutates.** Labels like `before-drain`
  and `after-drain` make the difference the proof.
- Nothing here needs a mock. The isolated home *is* the isolation.

Artifacts live at `<run>/artifacts/` and survive teardown.

## Cleanup

```
python .github/skills/verify-operator/control_operator.py down --run <run>
```

Removes `home/` and nothing else. Idempotent — running it twice says
`already removed`. **It never deletes `artifacts/`**, so proof outlives the run.

Both CLIs are short-lived, so there is no process to kill; `fleet run` without
`--rounds` is the one exception — it polls until `<home>/fleet.stop` appears. Use
`--rounds`, and if a run was started without it, create that marker file rather
than killing by name. Delete old run directories under `.verify-operator/` by hand
when the proof is no longer wanted.

## Helpers

`control_operator.py` is the only helper, and it ships no extension-specific
knowledge. `python control_operator.py --help` lists every verb; each verb takes
`--help` too.

| Verb | Purpose |
| --- | --- |
| `up` | create the disposable home and register the repo |
| `doctor` | read-only health check |
| `seat` | run `operator-seat` from the registered checkout (`--cwd` to override) |
| `fleet` | run `operator-fleet` against the run's home |
| `seed-ledger` | append records of any shape to `trace.jsonl` (fixture) |
| `seed-queue` | append proposals to `proposals.jsonl`; `--abandoned` leaves an orphaned `proposals.draining.*` batch, `--pad-to-bytes` fills it to the refusal limit (fixture) |
| `rotate-ledger` | rename `trace.jsonl` to `trace.jsonl.1`, as the appender does at 8 MB (fixture) |
| `enable` | turn any extension on by name (fixture) |
| `evidence` | snapshot home state under a label |
| `down` | remove the instance, keep the artifacts |

`seed-ledger`, `seed-queue` and `enable` are fixture setup, not user paths. They
exist so the behaviour *downstream* of them — the tail, the drain, the activation
gate — can be driven through the shipped commands. Their argument shapes are
deliberately generic: no record schema and no extension name is privileged.

The harness has its own tests, because a harness bug produces a *false* proof
rather than a failed one:

```
python -m pytest .github/skills/verify-operator/test_control_operator.py -q
```

They cover what the harness decides before it hands off to a subprocess — which
home the child is told to use, what lands in the activation file, what `evidence`
captures, and what `down` removes. They are deliberately outside the repository's
`testpaths`, so they never run in the kernel suite and never affect its budgets;
run them after changing `control_operator.py`.

## Features

The maintained map is [`features/README.md`](./features/README.md). Read the index,
then the matching feature file, before driving. A proof that exercised one entry
point is incomplete when the map lists others.

## Known trap in this repo

`e2e_restart_loop.py` at the repo root is **stale**. It resolves
`OP = REPO / "copilot_operator.py"`, and that module does not exist here — it lives
in the sibling repo `../copilot-tools`. Do not treat it as a working harness or
copy its paths; the restart-loop behaviour it describes belongs to that other
checkout.
