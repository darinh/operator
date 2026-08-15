# Tests waiting on behaviour that has not been ported yet

These are migrated but not yet running. Each is here because the behaviour it
encodes is not in the kernel, and the rule this extraction follows is that a
test travels with its behaviour rather than ahead of it. A test kept green by
weakening it would be worse than a test that is honestly not running.

They are **not** deleted, because each encodes an incident, and the incident
outlives the code that caused it.

## `test_restart_all_loops.py`

Needs `list_instances` and the CLI dispatch -- the board and the command
surface. Neither is supervision, and neither has been built here yet. The sweep
itself (`restart_all_loops`) *is* ported; only its presentation is missing.

Running it errors at collection, which is the honest signal. Note that `op.main`
is now *absent* rather than wrong: it used to resolve to the standard library's
`trace.main`, so `test_the_cli_routes_all_to_the_sweep` was calling into
Python's coverage tracer. See the shim note below.

## `test_loop_resilience.py` -- moved back on 2026-08-15

It is now `tests/test_loop_resilience.py` and all 26 of its tests pass. The
three things this file said it was waiting for turned out to be one thing
wearing three faces, and none of them was a missing port:

- **The FakeMux substitution fixture.** Not missing. `conftest` had one and it
  was *inert*: it assigned to `copilot_operator.MUX`, an attribute on the module
  this kernel was extracted from, in a repository that is not this one. The
  kernel reads `config.MUX`. Nineteen tests here failed loudly only because the
  guard's *other* half -- the spawn poison -- caught them on the way to the
  developer's real multiplexer. It now writes through `op`, which forwards to
  every module binding the name, and `tests/test_mux_isolation.py` grades it.
- **`show_run_summary`.** Also not missing: it is in `operator_kernel/paths.py`.
  This entry was written from the extraction plan rather than from the tree.
- **Project-keyed handoff discovery.** Present too, in `exits.py`
  (`handoff_state`, `crash_recovery_verdict`).

What was actually broken was `tests/op.py`, which mapped `operator_trace` to a
module named `trace`. There is no `operator_kernel/trace.py`, so that import
resolved to the **standard library's** tracing module and bound its contents
into the kernel namespace. The four tests using `operator_trace` were asking
Python's coverage tracer for `trace_path`. The behaviour they want is in
`evidence.py`, and the alias now points there.

The file moved back with one edit, and it is worth being precise about what kind
of edit it was, because the rule below forbids the other kind. Four occurrences
of `import operator_trace` -- a bare import that resolved to
`../copilot-tools/operator_trace.py` -- became
`operator_trace = op.operator_trace`. No assertion was touched and no
expectation relaxed; the suite went from 19 loud failures plus 4 wrong-module
errors to 26 passing without any assertion being made easier to satisfy.

## The rule for taking one out of here

Port the behaviour, then move the test back and run it unmodified. If it needs
editing to pass, the port is not finished -- that is the signal, not an
inconvenience.

Repointing an import that named **another repository** is not that kind of
editing, and the distinction is worth writing down: the rule protects against
weakening a test until it agrees with the code, whereas an import fixed here
changes *which code the test grades at all*. A test importing its subject from
the checkout next door was never grading this port in the first place.
