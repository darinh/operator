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

## What it detects, and what it does not

Within one writer's chain it detects an edited payload, a deleted record that
had a successor, and reordering.

It does not detect these, and a verifier will return `Verified`:

- An entire writer's records removed. Nothing else references them, so their
  absence leaves no hole.
- A writer's trailing records removed. There is no successor left to mismatch.
- Unchained records inserted. They are indistinguishable from the pre-chain
  records a real ledger legitimately contains.
- Reordering *across* writers. Order between writers is file order, which the
  chain does not cover.

The first two matter for rotation. A `Gap` is reported only when surviving
records sit on both sides of the loss, so a rotation that discards a generation
containing only finished writers is invisible. Do not read a `Verified` as
"nothing was lost".

Detecting those needs a witness outside the file, which is the next section.

## Rotation

The chain is bound to the log, not to the file. A writer survives rotation, so
the sequence continues across `trace.jsonl.1` then `trace.jsonl`. The verifier
reads both as one stream.

Rotation keeps one generation. A second rotation destroys the first. When
surviving records bracket the loss the verifier reports `Gap`, never
`Verified`. When they do not, see the limits above.

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
