# The evidence ledger chain

`trace.jsonl` carries a per-writer hash chain. It is a checksum, not
tamper-evidence.

A local unsigned hash chain detects accidental corruption, partial writes, and
careless editing. It detects nothing against anyone with write access to the
file, because re-chaining the whole log takes about five seconds.

## What is chained

Every record `evidence._append` writes to the ledger, when the caller passes
`chain=True`, gains:

```
"chain": {"w": <writer id>, "n": <seq>, "p": <prev digest or null>, "d": <digest>}
```

`w` is one process run, `pid` plus a token, because pids are reused. `n` starts
at 1 and steps by 1. `p` is the previous `d` for that writer, or null on the
first record. `d` is `sha256(p_or_empty + canonical_json(record_without_chain))`.

The seat journal and the proposal queue call `_append` without that flag. They
must not grow a chain field.

## Why the chain is per writer

Every supervisor process appends to the same `trace.jsonl`. `_append` takes no
lock. A single global sequence would race: two processes would each believe they
were chaining from record N.

Each writer keeps its own chain. Records interleave in the file. A verifier
groups by `w` before it checks continuity.

## Rotation

The chain is bound to the log, not to the file. A writer survives rotation, so
the sequence continues across `trace.jsonl.1` then `trace.jsonl`. The verifier
reads both as one stream.

Rotation keeps one generation. A second rotation destroys the first. That is
permanent loss. The verifier reports a gap in `n` as `Gap`, never as `Verified`.

A torn final line, at most one and only at the very end, is `TruncatedTail`,
not `Broken`.

Ledgers written before this chain exist. Those files report `NoChain`. Mixing
an unchained prefix with chained records is normal and must verify.

## What would actually change the threat model

Escape the digest. Periodically copy `(writer, seq, digest)` somewhere the
logging process cannot rewrite. Any single retained old digest turns an
undetectable rewrite into a provable one. NIST SP 800-53 AU-9(2), "Store on
Separate Physical Systems or Components", is the control-language version.

Do not implement that here. It is the next step, not this one.
