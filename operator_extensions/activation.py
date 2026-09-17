"""Which of these extensions a human has turned on, and with what settings.

Installing a package must not change how the fleet behaves. That is not a
preference: `admit_launch` sits on the launch path of every seat and its
refusals are honoured, so an extension that starts answering the moment `pip`
finishes is one install away from holding every seat closed -- and the operator
log names *who* refused and never *why*, so the person debugging it at 3am has
a package name and nothing else.

So activation is a file a human writes, at `~/.operator/extensions.json`:

    {
      "worktree-guard":   {"enabled": true},
      "worktree-janitor": {"enabled": true, "roots": ["~/repos"]},
      "seat-watch":       {"enabled": true, "failures": 3}
    }

**Every failure to read that file means "not enabled".** A missing file, a
syntax error, a permission denial, a value of the wrong shape -- all of them
produce silence rather than a guess. This is the fail-open direction the design
requires of an extension (§D-3: nothing may fail *closed* on an extension's
absence), and it is also the only safe reading of a corrupt config: a truncated
JSON file must not be able to decide that a gate is on.

`COPILOT_OPERATOR_HOME` is honoured because the kernel honours it, and a test
that relocates the whole tree has to be able to relocate this too. Resolved on
every call rather than captured at import, for the reason `paths.py` gives about
its own home: a module-level constant does not follow a `monkeypatch`, and three
tests in this repository already read the developer's real `~/.operator` because
of exactly that mistake.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

#: The file a human writes to turn one of these on. One file rather than one
#: per extension: the question "what is currently allowed to answer a hook?"
#: should have a single place to look, and a directory of small files answers
#: it only for somebody who already knows what to expect in it.
CONFIG_NAME = "extensions.json"


def operator_home() -> Path:
    """`~/.operator`, or wherever `COPILOT_OPERATOR_HOME` points.

    A deliberate copy of `config.operator_home()` rather than an import of it.
    An extension is third-party code that happens to live in this repository,
    and one that imports the kernel to find a directory is modelling a coupling
    no installed package can have. The path is part of the kernel's published
    interface -- it is in the deployed instructions and in `docs/` -- so the
    duplication is of a documented constant, not of logic.
    """
    override = os.environ.get("COPILOT_OPERATOR_HOME")
    return Path(override) if override else Path.home() / ".operator"


def config_path() -> Path:
    return operator_home() / CONFIG_NAME


def _load() -> dict:
    """The whole config, or an empty one. Never raises, never guesses."""
    try:
        raw = config_path().read_text(encoding="utf-8")
    except (OSError, ValueError):
        return {}
    try:
        parsed = json.loads(raw)
    except ValueError:
        # A truncated or hand-broken file. Reported nowhere, because there is
        # nowhere here to report to that a human reads -- and answering "no
        # opinion" is the outcome that changes nothing, which is what an
        # unreadable configuration should do.
        return {}
    return parsed if isinstance(parsed, dict) else {}


def settings(name: str) -> "dict[str, Any] | None":
    """This extension's settings if a human enabled it, else None.

    `None` rather than an empty dict, so a caller cannot accidentally treat
    "not configured" as "configured with defaults" -- the two differ by whether
    somebody made a decision, and every hook here returns no opinion for the
    first.

    `enabled` must be exactly `True`. A string `"true"`, a `1`, or a dict with
    settings but no `enabled` key are all refused: this flag is the only thing
    standing between an installed package and the launch path, so it is read
    strictly rather than truthily.
    """
    entry = _load().get(name)
    if not isinstance(entry, dict) or entry.get("enabled") is not True:
        return None
    return entry


def roots(config: "dict[str, Any]", key: str = "roots") -> "list[Path]":
    """Configured directories to look in, expanded and made absolute.

    Anything that is not a usable path is dropped rather than raising: this is
    read from a hand-edited file, and one bad entry among five must not stop the
    other four from being examined.
    """
    found: list[Path] = []
    values = config.get(key)
    if isinstance(values, str):
        values = [values]
    if not isinstance(values, list):
        return found
    for value in values:
        if not isinstance(value, str) or not value.strip():
            continue
        try:
            found.append(Path(value).expanduser())
        except (OSError, ValueError, RuntimeError):
            continue
    return found


def state_path(name: str) -> Path:
    """Where an extension may keep what it must remember between calls.

    It has to keep it *somewhere*: `extensions.Host` spawns one process per
    call, so nothing an extension holds in memory survives to the next question.
    That is a property worth having -- no extension can leave state behind that
    another call reads -- and it means anything genuinely cumulative, like
    `seat_watch`'s failure counts, is a file or it does not exist.

    Under the operator home rather than beside the installed package, because
    the package directory may be read-only and is certainly not per-machine
    state.
    """
    return operator_home() / "extensions" / f"{name}.json"


def read_state(name: str) -> dict:
    """Whatever was last written, or an empty dict. Never raises."""
    try:
        parsed = json.loads(state_path(name).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def write_state(name: str, state: dict) -> bool:
    """Replace this extension's state. Returns whether it was written.

    Written to a temporary file and moved into place, because the reader is a
    process that may start at any moment: a plain truncate-and-write leaves a
    window in which `read_state` sees half a JSON document, and `read_state`
    turns that into an empty dict -- silently losing counts rather than being
    seen to lose them.
    """
    path = state_path(name)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(state), encoding="utf-8")
        os.replace(tmp, path)
        return True
    except (OSError, ValueError, TypeError):
        try:
            tmp.unlink()
        except OSError:
            pass
        return False
