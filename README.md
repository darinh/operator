# operator

Supervision for fleets of autonomous coding agents.

You give an agent a repository and walk away. `operator` is what notices when it stops making
progress, restarts it, brings it back after a reboot, and keeps a checked record of what
happened. It writes no code itself.

The unit it supervises is a **seat**, not a session. A seat outlives the sessions it runs, keeps
its own memory across them, and accumulates knowledge of a project the longer it works there.

## Install

```
pip install -e .
operator doctor
```

`doctor` names anything missing and how to install it. It needs the GitHub Copilot CLI on PATH
and a terminal multiplexer (`tmux`, or `psmux` on Windows).

Python 3.10 or newer. Nothing else is required. The tool stands alone.

## Quickstart

```
cd ~/repos/yourproject
operator project register        let this directory hold seat memory
operator start --name alpha      start a seat, give it the first task
operator list                    confirm it is running
operator join alpha              watch it work
```

Come back later and `operator recall --instance alpha` shows what that seat learned.

## Use it

Run `operator` with no arguments and you get a menu. Every menu action prints the command it
ran, so the flags are learnable by use rather than by memorising.

```
operator                      the menu
operator doctor               is this machine ready
operator project register     let this directory hold seat memory
operator start --name alpha   start a supervised seat here
operator list                 what is running
operator join alpha           attach your terminal to it
operator stop alpha           ask its supervisor to stop
operator recover --all        bring back seats a crash or reboot took down
operator handoff --instance alpha --status "what you did"
                              end this session, leave the next one a record
operator trace                what happened, newest first
operator verify               has the record been altered
```

## What it costs

Each seat runs a real GitHub Copilot CLI session, and the supervisor restarts it when a session
ends, up to 1000 times. Three seats left overnight is three agents working continuously against
your account. Nothing here bills you directly, but nothing here caps your usage either. The
spend ceiling is unlimited by default and you set it yourself.

## What it actually does

**Stops a seat that has stopped working.** Three independent counters run, and whichever reaches
its limit first ends the run.

| Counter | Limit | Counts | Exit |
| --- | --- | --- | --- |
| crash loop | 5 | unexpected exits inside 120 seconds | 1 |
| no progress | 3 | sessions that changed nothing and ended cleanly | 3 |
| unaccounted | 5 | sessions that changed nothing and ended without a handoff or observed exit | 4 |

The fingerprint behind the last two covers the whole repository, every ref and every worktree,
so a seat that keeps producing work trips neither. The crash counter is checked first, which
means a run of short unaccounted sessions ends as a crash loop rather than as unaccounted
endings.

**Knows the difference between "nothing changed" and "I could not tell."** An unreadable git
probe is `unknown`, not `unchanged`, and it advances no counter. A breaker that fires on its own
blindness would stop a healthy fleet.

**Survives a reboot.** `operator recover` lists seats that were supervised when the machine
stopped. `operator recover --all`, or a seat name, restarts their supervisors, continuing the
session numbering rather than starting over.

**Keeps a record you can check.** Every supervision fact appends to `~/.operator/trace.jsonl`
with a per-writer hash chain, and `operator verify` replays it.

Read `docs/ledger.md` before trusting that, because the limits are real. It is a checksum, not
tamper-evidence. It catches an edited record and a record removed from the middle of a writer's
run. It does **not** catch a whole writer removed, or records removed from the end of one, and
it detects nothing at all against someone who can rewrite the file, because re-chaining it takes
seconds.

**Remembers.** A seat records decisions, gotchas and dispositions with `operator remember`, and
reads them back with `operator recall`. That memory is per seat and per project, keyed on the
seat's identity rather than any session, and it is the reason a seat gets better at a repository
over time.

## Extensions

Three ship with the tool and all three are **inert until you enable them**.

```
operator ext list
operator ext enable worktree-janitor
```

That default is deliberate. Extensions answer `admit_launch`, which can refuse to start a seat,
so an extension that went live on install would be one `pip install` away from holding an entire
fleet closed. Enabling stays an explicit act.

Extension code never runs inside a supervisor process. Each call gets its own worker process
with a deadline, because cancelling a thread blocked in native code is not possible on Windows,
which makes an in-process deadline aspirational. An extension that errors is recorded as having
errored, and is never treated as having said no.

It is not a sandbox and is never described as one. A worker runs as you and can read what you
can read. `docs/extensions.md` has the full contract.

## Does a change to this make it better or worse

`operator_bench` runs the real supervisor against scripted seats whose true behaviour is known,
and scores its decisions. No LLM, no API spend, six scenarios in about 16 seconds.

```
python -m operator_bench measure
```

Every number carries its sample size, coverage, label source and an interval, or reports
`NoEstimate` with a reason. Miss rate and false-alarm rate stay separate, because lowering the
stall threshold improves one and destroys the other. One scenario is a seat that manufactures
busywork, and it scores a **miss** on purpose, because the supervisor genuinely cannot tell.

## What it does not do

Stated plainly, because each of these looks built from the outside.

**No seat is given work beyond its first task.** You tell a seat what to work on when you start
it, and that prompt reaches the session. What does not exist is anything that hands a seat its
*next* piece of work. `work_seam.set_session_store` is called by nothing, so `session_store()`
returns `None` on every run. Once the opening task is done, later sessions continue with
whatever the seat writes in its own handoff, not from a queue anyone else controls.

**There is no intent layer.** Nothing traces a change back to an authorised goal. The evidence
ledger records what happened, not whether anyone asked for it.

**There is no trust boundary.** Agents run under your filesystem identity. A seat can write the
mandate, the gate code and the ledger. Making "an agent cannot grant itself authority" true
rather than decorative needs a separate OS account and ACLs.

**There is no morning report.** After an overnight run, `operator list` shows what is still up
and `operator trace` shows raw records. Nothing summarises which seats ran, what they produced,
or why each one stopped.

**Two extension hooks are declared and never asked.** `gate_change` and `detect_repo` are part
of the documented hook set with no call site, because there is no kernel merge gate to hang the
first on. Only `admit_launch` is live.

**Quality is not measured.** The progress breaker detects motion, never merit. A seat committing
plausible slop forever looks healthy.

## Layout

| | |
| --- | --- |
| `operator_kernel/` | supervision. The loop, the breakers, the ledger, the launch gate |
| `operator_fleet/` | the fleet host, which tails the ledger and drains a proposal queue |
| `operator_cli/` | entry points, including the menu |
| `operator_extensions/` | the three reference extensions |
| `operator_memory/` | the seat journal |
| `operator_bench/` | the benchmark harness |

The kernel may not import the fleet, and nothing may import the bench. Each package has a line
budget enforced by a boundary test, so adding code sometimes means extracting a seam first
rather than raising a number.

## Contributing

`python -m pytest -q` runs the suite. CI is Windows and Linux on Python 3.10 and 3.12.

Before opening a PR, read `.github/skills/pr-gate/SKILL.md` and run
`python .github/skills/pr-gate/preflight.py --pr <number>`. Self-merge additionally needs two
reviewers from different model families to approve.

`docs/plan.md` is the design program and records what was decided and what was withdrawn.
