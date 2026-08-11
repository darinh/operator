# Citation verification — R1b (gating round)

Run 2026-08-10. A research agent returned confident citations; this checked each
against a primary source before any of it was allowed to influence a decision.

## Verdicts

| Claim | Verdict | Source |
|---|---|---|
| Claude Code documented session resume | **CONFIRMED** | code.claude.com/docs/en/sessions |
| GitHub Copilot CLI `--resume` / `--continue` | **CONFIRMED** | docs.github.com, Copilot CLI overview |
| Gemini CLI `--resume <uuid>` + checkpoint tags | **CONFIRMED** | google-gemini/gemini-cli docs |
| OpenAI Codex CLI `codex resume --last` / `<id>` | **CONFIRMED** (secondary docs) | github.com/openai/codex |
| Aider `--restore-chat-history` | **CONFIRMED** (weaker: no session picker) | aider.chat/docs |
| Cursor CLI resume | **NOT VERIFIED** — IDE-first, no CLI resume found | — |
| `wan9yu/cli-agent-runner` exists | **CONFIRMED**, Apache-2, **POSIX-only** | github.com/wan9yu/cli-agent-runner |
| arXiv:2602.11988 exists, authors, dates | **CONFIRMED** | arxiv.org/abs/2602.11988 |
| That paper's abstract: >20% cost, no general gain | **CONFIRMED, quoted** | same |
| Its *specific* figures (1.6x, 2.45-3.92 steps, 641 words) | **UNVERIFIED** — body/tables, PDF not extractable | — |
| MCP spec revision 2026-07-28 | **CONFIRMED** | modelcontextprotocol.io |
| MCP as an inter-agent messaging substrate | **NOT CONFIRMED** — and the 2026-07-28 revision removed session ids and the initialize handshake, making it *more* stateless | same |
| "AGENTS.md, 25+ platforms" | **PARTIAL** — 3-6 tools with official docs; "25+" is an aggregator claim | — |
| GitHub blog "lessons from 2500 repositories" | **EXISTS BUT DIFFERENT** — it is about Copilot *custom agent persona* files, a different feature with a similar filename | github.blog |

## The decision this changes

The plan chose an external durable ledger as the continuity mechanism and
demoted harness-native resume to "an optional adapter capability". Four of six
harnesses have documented, first-class session resume. That is a convergent
industry standard, not an edge case, and the demotion was wrong as stated.

But the greenfield design's *argument* against resume survives verification, and
it is the important half:

> resuming an exhausted context just re-exhausts it

Claude Code's resume restores full conversation history, tool calls and results.
If the session ended **because it ran out of context**, restoring all of it puts
the next session straight back at the wall.

So neither mechanism is right on its own, and the ending reason picks:

| Ending | Mechanism | Why |
|---|---|---|
| crashed / killed, context remaining | **harness-native resume** | complete, cheap, no agent involvement, nothing to compose or trust |
| context exhausted | **composed briefing from the ledger** | a fresh context that must be *smaller* than what filled the last one |
| finished | neither — new work, new session | |

The supervisor already classifies the ending, so it already holds the input this
switch needs. This is better than either mechanism alone and it came out of
verifying a citation rather than out of the design.

**Immediately actionable:** Copilot CLI supports `--resume` / `--continue`
today, and the system being replaced does not use it — it relaunches fresh with
a preamble every time. That is available now, for the dominant crash case.

## Build vs adopt

`wan9yu/cli-agent-runner` is real: Apache-2, restart-on-exit supervision, presets
for several agent CLIs, systemd units, and a catalogue of 13 named defenses
(the earlier report's "11+ layers" conflated its 3 architectural layers with 11
notify-only detectors).

It is **POSIX-only**, and Windows is the primary machine here, so adopting it
wholesale is not available. That is a reason not to adopt, not a reason not to
read it: 13 named defenses against agent failure modes is a design input, and
this project should compare its own against that list before claiming coverage.

## Corrections carried back

- `docs/rationale.md` in the source repo asserts precise figures from this paper.
  The abstract is confirmed; the figures are from the body and could not be
  extracted. They should be cited as from-the-paper rather than restated as
  independently established.
- Do not cite the GitHub "2500 repositories" post as evidence about AGENTS.md
  instruction files. It is about a different feature.
- Do not plan on MCP as a coordination bus. The current revision moved away from
  the statefulness that would require.
