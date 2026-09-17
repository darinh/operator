"""Entry points. The one thing this repository had none of.

Every piece of the extension system was reachable from a test and from nothing
else: `FleetHost.run()` existed and no process called it, `proposals.jsonl` was
written and nothing read it back. Both were recorded in `docs/extensions.md` §8
as open assumptions, and both had the same cause -- the command-line interface
this kernel was extracted from stayed behind in `copilot-tools`, so there was
nowhere for a process to start.

This package is that somewhere. It is deliberately thin: it parses arguments,
resolves a home directory and calls into the kernel and the fleet. Anything with
a decision in it belongs one layer down, where the budgets and the boundary
tests are.

It is a separate package rather than an `operator_fleet/__main__.py` because the
fleet's ceilings are set for the fleet's job -- `MAX_FLEET_TOTAL_LINES` had 130
lines of headroom when this was written, and squeezing a command line into that
would have meant deleting explanation to fit, which `test_kernel_boundary.py`
names as the most damaging edit available in this repository.
"""
