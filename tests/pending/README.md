# Tests waiting on behaviour that has not been ported yet

These are migrated but not yet running. Each is here because the behaviour it
encodes is not in the kernel, and the rule this extraction follows is that a
test travels with its behaviour rather than ahead of it. A test kept green by
weakening it would be worse than a test that is honestly not running.

They are **not** deleted, because each encodes an incident, and the incident
outlives the code that caused it.

## `test_loop_resilience.py`

Needs three things the kernel does not have:

- **A FakeMux substitution fixture.** Nineteen of its tests reach for a real
  multiplexer. The guard that catches them was ported and works -- that is why
  they fail loudly rather than driving this machine's tmux. What is missing is
  the autouse fixture that puts a fake in its place.
- **`show_run_summary`.** Several tests patch it. It was the metrics summary and
  it is deliberately not in the kernel.
- **Project-keyed handoff discovery.** Four tests exercise
  `crash_recovery_verdict` across a project catalogue. Handoff is being replaced
  by the ledger, so these should be rewritten against that rather than ported.

## `test_restart_all_loops.py`

Needs `list_instances` and the CLI dispatch -- the board and the command
surface. Neither is supervision, and neither has been built here yet. The sweep
itself (`restart_all_loops`) *is* ported; only its presentation is missing.

## The rule for taking one out of here

Port the behaviour, then move the test back and run it unmodified. If it needs
editing to pass, the port is not finished -- that is the signal, not an
inconvenience.
