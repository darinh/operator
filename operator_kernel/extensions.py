"""Extension points, and the two things a plugin may never do.

A plugin system is how this stops being one person's tool. It is also the most
direct route back to every failure this kernel was extracted to prevent, so the
shape matters more than the mechanism.

**extensions load at runtime, never by import.** `tests/test_kernel_boundary.py`
forbids the kernel importing anything outside the standard library and itself,
and that is not an obstacle to work around — it is what keeps the kernel a
kernel. So nothing here is imported by kernel code; extensions are discovered,
loaded through this module, and called through narrow interfaces.

**Two prohibitions, enforced rather than documented.**

1. *A plugin may not grant authority.* Backlog 0013: a sentence claiming
   blanket human approval reached every session's launch instructions, authored
   by an agent and never by the owner. A plugin that can contribute preamble
   text is a second way to write that sentence, with a package name in front of
   it for credibility. So a plugin's text is carried as a **claim attributed to
   the plugin**, and the authority composer drops anything without human
   provenance — the same rule that applies to agents, applied to code somebody
   installed.

2. *A plugin may not weaken a gate.* Gates decide whether work is admissible.
   A plugin that can remove or relax one can make anything admissible, which is
   the whole verification story defeated by an entry in `pyproject.toml`.
   `Gate` contributions are therefore **additive only**: a plugin may add a
   check that must pass, never remove or override one.

**A plugin's failure is the plugin's, not the fleet's.** Anything raised while
loading or calling a plugin is caught, recorded against that plugin, and the
kernel carries on without it. The alternative is that installing a third-party
package can stop nine supervised seats, which no plugin author should be able
to do by accident.

**What a plugin cannot see** is as important as what it can do. Hooks receive
explicit, narrow arguments — never the `Instance`, never a path to the state
directory, never the ledger. A hook that is handed the world will eventually
write to it.
"""
from __future__ import annotations

import dataclasses
import importlib
import importlib.metadata
import traceback
from typing import Any, Callable, Iterable

#: The entry-point group extensions register under.
ENTRY_POINT_GROUP = "operator_kernel.plugins"

#: Hooks a plugin may implement. Deliberately a closed set: an open registry
#: where a plugin can name its own hook is a registry where a plugin can reach
#: anywhere the kernel later grows a call site.
#:
#: `harness`  — how to launch a harness, detect why its session ended, and
#:              whether it supports native resume. The seam that makes this
#:              harness-agnostic rather than Copilot-shaped.
#: `gate`     — an additional check a change must pass. Additive only.
#: `notify`   — told that something happened. Cannot influence anything.
#: `briefing` — contributes *facts* to a session briefing. Never authority.
HOOKS = ("harness", "gate", "notify", "briefing")


class PluginError(Exception):
    """A plugin misbehaved. Never raised out of the kernel's own paths."""


@dataclasses.dataclass(frozen=True)
class Contribution:
    """One thing a plugin provided, with the plugin named on it.

    Provenance is not decoration. Every piece of text or judgement that reaches
    a session or a decision has to be answerable for, and `plugin` is that
    answer. Nothing here is anonymous.
    """

    plugin: str
    hook: str
    value: Any


@dataclasses.dataclass(frozen=True)
class LoadFailure:
    """A plugin that could not be loaded, and why.

    Recorded rather than raised, and *reported* rather than logged and
    forgotten: a plugin that silently does not load is indistinguishable from a
    plugin that loaded and had nothing to say, which is this project's oldest
    failure wearing a new coat.
    """

    name: str
    error: str
    detail: str


def discover(entry_points: Iterable | None = None) -> tuple[dict, list[LoadFailure]]:
    """Load every registered plugin. Returns ``(extensions, failures)``.

    Never raises. A plugin that explodes on import is a failure recorded
    against its own name, not an outage for the fleet.
    """
    extensions: dict[str, Any] = {}
    failures: list[LoadFailure] = []
    if entry_points is None:
        try:
            entry_points = importlib.metadata.entry_points(group=ENTRY_POINT_GROUP)
        except Exception as exc:  # pragma: no cover - environment dependent
            return {}, [LoadFailure("<discovery>", type(exc).__name__, str(exc))]
    for ep in entry_points:
        try:
            extensions[ep.name] = ep.load()
        except Exception as exc:
            failures.append(
                LoadFailure(ep.name, type(exc).__name__,
                            traceback.format_exc(limit=3))
            )
    return extensions, failures


def collect(extensions: dict, hook: str, /, **kwargs) -> tuple[list[Contribution],
                                                            list[LoadFailure]]:
    """Call ``hook`` on every plugin that implements it.

    Keyword-only arguments, and the caller decides what they are: a hook that
    is handed the `Instance` or the state directory can write to them, and no
    amount of documentation prevents that.
    """
    if hook not in HOOKS:
        raise PluginError(
            f"{hook!r} is not a hook. The set is closed ({', '.join(HOOKS)}) so "
            f"that a plugin cannot name its way into a call site the kernel "
            f"grows later."
        )
    out: list[Contribution] = []
    failures: list[LoadFailure] = []
    for name, plugin in sorted(extensions.items()):
        fn: Callable | None = getattr(plugin, hook, None)
        if fn is None:
            continue
        try:
            value = fn(**kwargs)
        except Exception as exc:
            failures.append(
                LoadFailure(name, type(exc).__name__, traceback.format_exc(limit=3))
            )
            continue
        if value is not None:
            out.append(Contribution(plugin=name, hook=hook, value=value))
    return out, failures


def briefing_text(contributions: Iterable[Contribution]) -> str:
    """Plugin briefing contributions, rendered as attributed claims.

    Attribution is the enforcement. A plugin's text arrives labelled with the
    plugin that wrote it and marked unverified, so it cannot be read as
    something the kernel observed or the owner authorised. Backlog 0013 is one
    unattributed sentence reaching every session; this is the same sentence
    with a name in front of it, which is the difference between a claim and an
    instruction.
    """
    lines = []
    for c in contributions:
        if c.hook != "briefing":
            continue
        text = str(c.value).strip()
        if text:
            lines.append(f"[plugin {c.plugin}, unverified] {text}")
    return "\n".join(lines)


def gate_checks(contributions: Iterable[Contribution]) -> list[Contribution]:
    """Plugin gates, which are additive only.

    There is no mechanism here for removing or overriding a kernel gate, and
    that absence is the design. A plugin that could relax a gate could make
    anything admissible, and the entire verification story would rest on what
    is installed.
    """
    return [c for c in contributions if c.hook == "gate"]
