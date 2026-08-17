"""Reference extensions for `operator_kernel.extensions`, and the proof it works.

These are the first things ever to answer one of the kernel's hooks. They exist
for two reasons, and the second is the load-bearing one:

1. Git worktrees are a real operational problem in this fleet -- a seat launched
   into a half-finished merge, a checkout whose branch landed weeks ago and
   whose directory nobody removed.
2. **A hook nothing implements is a hook nobody has tested.** `docs/extensions.md`
   was a design and `tests/test_extensions.py` was a design's tests: every
   extension in them was written by the test that asked it a question. Code that
   has to survive discovery, packaging, a spawned interpreter and a JSON
   boundary without a test author's cooperation is a different claim.

**Nothing here imports the kernel, and that is checked** (see
`tests/test_extension_packaging.py`). An extension is third-party code by
definition; one that reaches into `config.py` for `OPERATOR_HOME` would be
demonstrating a coupling no real extension can have, and the reference
implementation would be teaching the wrong lesson. The duplication of
`~/.operator` resolution in `activation.py` is therefore deliberate rather than
an oversight -- it is what an outside package actually has to do.

**Everything here ships inert.** Registering an entry point is enough to be
asked a question on the launch path of every seat, and `admit_launch` refusals
are *honoured* -- so an extension that is live the moment it is installed is one
`pip install` away from holding a nine-seat fleet closed. Activation is a file a
human writes; see `activation.py`.
"""
