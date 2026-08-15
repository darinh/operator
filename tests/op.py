"""`op` for ported tests: the kernel presented as one namespace.

The tests being migrated were written against a single 9,120-line module and
reference it as `op`. Rewriting several thousand assertions to chase the new
module boundaries would be a large, silent, error-prone edit of the exact
artifacts that encode *why* the behaviour is what it is -- so the boundaries are
presented rather than rewritten. The tests move unmodified, which is precisely
what makes them evidence that the behaviour survived the move.

**This forwards writes, and that is the whole design.** A namespace that merely
copied names in would let `monkeypatch.setattr(op, "RESTART_DIR", tmp)` succeed
and change nothing, because `instance.py` reads `config.RESTART_DIR`. Every test
that relocates state would then run against the developer's real `~/.operator`
while reporting a pass -- a shim that silently does nothing is worse than no
shim, and it is this project's signature failure wearing a test helper's
clothes. So `__setattr__` writes through to whichever module owns the name, and
`test_op_shim.py` asserts that it does.
"""
from __future__ import annotations

import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "operator_kernel"))

_MODULE_NAMES = (
    "config", "paths", "gitio", "probes", "presence", "instance", "launch",
    "session_state", "provenance", "supervisor_records", "breakers", "exits",
    "preamble", "supervisor", "supervisor_control", "trace", "claims",
    "snapshot", "process_identity", "mux", "console", "sqlite_store",
    "version", "mandate",
)

_ALIASES = {
    "operator_liveness": "process_identity",
    "install_manifest": "presence",
    "operator_trace": "trace",
    "work_claims": "claims",
}


class _KernelNamespace(types.ModuleType):
    """One namespace over many modules, with writes forwarded to the owner."""

    def __init__(self, name):
        super().__init__(name)
        self.__dict__["_owner"] = {}

    def _bind(self):
        holders = self.__dict__["_owner"]
        for mod_name in _MODULE_NAMES:
            module = __import__(mod_name)
            self.__dict__[mod_name] = module
            for key, value in vars(module).items():
                if key.startswith("__"):
                    continue
                # EVERY module binding the name, not just the one that defines
                # it. `instance.py` does `from config import RESTART_DIR`, so it
                # holds its own reference; writing only to `config` would leave
                # the reader untouched and the patch silently inert. In the
                # single module these tests were written against, one global
                # served both -- restoring that property is the shim's job.
                holders.setdefault(key, []).append(module)
                self.__dict__.setdefault(key, value)
        for old, new in _ALIASES.items():
            self.__dict__[old] = self.__dict__[new]

    def __setattr__(self, key, value):
        for module in self.__dict__.get("_owner", {}).get(key, ()):
            setattr(module, key, value)
        self.__dict__[key] = value

    def __delattr__(self, key):
        for module in self.__dict__.get("_owner", {}).get(key, ()):
            if hasattr(module, key):
                delattr(module, key)
        self.__dict__.pop(key, None)

    def holders_of(self, key):
        """Every kernel module binding `key`. Used by the shim's own tests."""
        return tuple(self.__dict__["_owner"].get(key, ()))


_ns = _KernelNamespace(__name__)
_ns.__dict__["_MODULE_NAMES"] = _MODULE_NAMES
_ns.__dict__["_ALIASES"] = _ALIASES
_ns.__dict__["Path"] = Path
_ns.__dict__["sys"] = sys
_ns._bind()
sys.modules[__name__] = _ns
