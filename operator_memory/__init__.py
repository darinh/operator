"""A seat's memory of itself. The substrate for `docs/seat-identity.md`.

Separate from the kernel because it is not supervision: nothing here observes a
process, decides a launch or classifies an exit. Separate from
`operator_extensions/` because it is *ours* -- it reads the project catalog and
appends through `evidence`, which an installed third-party package could not and
should not do.

It imports the kernel and the kernel does not import it, which is the same arrow
`operator_fleet/` sits on, held by the same boundary test.

**This package writes. It does not decide.** A journal entry is a claim a seat
made about its own past, stored so a later session can read it and check it. It
is never evidence, never authority, and never a substitute for looking at the
repository — which is the only thing here that is actually true.
"""
