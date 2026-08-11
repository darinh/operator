# Extraction spike — result: PASS

Run 2026-08-10. Gates the rewrite-vs-evolve decision and the new kernel repo.

## Question

Can the supervision kernel be separated from `copilot_operator.py` (9,120 lines)
and the 21 modules it imports, or does coupling make extraction a fiction?

## Findings

### 1. The god module is a leaf

`copilot_operator` imports 21 first-party modules. **Nothing imports it** except
`verify_cross_platform.py`, a standalone script. Extracting it breaks no
dependents — the dependency arrow points only outward.

### 2. The kernel is 32% of the module

Measured by AST over 233 top-level definitions:

| | defs | lines |
|---|---|---|
| supervision kernel | 45 | 2,503 |
| everything else | 188 | 5,410 |

Plus the already-clean modules — `operator_mux` (409), `operator_trace` (715),
`operator_liveness` (708), `operator_runner` (631), `install_manifest` (547),
`work_claims` (379), `operator_console` (36).

**Kernel repo estimate ~6,000 lines against 28,443 — a 4.7x reduction**, which
matches the autopsy's claim that "the process-supervision kernel is much smaller
than the repository".

### 3. One import blocks separation, and it is trivial

With every non-kernel module blocked by a `sys.meta_path` hook, 7 of 9 kernel
modules imported clean. Both failures were the same line:

    work_claims.py:39  from operator_ingest import connect

`operator_liveness` failed only transitively through `work_claims`.
`operator_session` has the identical import. `operator_runner`'s use of
`operator_ingest` is lazy and inside the metrics capture that is being dropped.

So the kernel's only tie to the metrics subsystem is **one SQLite connection
helper that happens to live in the metrics module**.

### 4. The fix was applied and verified

Extracting `connect` into a 1,089-byte `sqlite_store` module and repointing the
import gives:

    SPIKE RESULT: 10/10 kernel modules import with all non-kernel modules blocked
      and it still works: True

(the second line opens a real database through the extracted helper and reads
back what it wrote).

## Verdict

**Extraction is viable. The new kernel repo is justified.** Coupling does not
prevent it; one function does, and that function is 30 lines.

## Method note — the first two probes were false passes

Worth recording, because it is the failure class this whole project is about.

- **Probe 1** copied the kernel modules to a temp directory and imported them:
  9/9 pass. False. `copilot-tools` is pip-installed editable, so `work_claims`
  resolved `operator_ingest` from the live repo. Verified by printing
  `__file__`: `operator_ingest -> C:\Users\darin\repos\copilot-tools\operator_ingest.py`.
- **Probe 2** tried to filter the repo off `sys.path`. It reported
  `removed -1 entries` — it matched nothing, because an editable install uses a
  finder hook rather than a path entry. Its assertion also only checked each
  kernel module's own `__file__`, never its transitive imports, so it passed
  while proving nothing.
- **Probe 3** blocked the modules explicitly at `sys.meta_path` and produced the
  real answer: 7/9.

A probe that cannot fail reads exactly like a probe that passed. Two did, in a
row, on a question with a clean mechanical answer available.
