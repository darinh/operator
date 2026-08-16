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
    "preamble", "supervisor", "supervisor_control", "evidence", "claims",
    "snapshot", "process_identity", "mux", "console", "sqlite_store",
    "version", "mandate", "work_seam", "extension_seam",
)

#: Names the tests were written against, mapped to what the kernel calls them
#: now. A rename during extraction is exactly the sort of thing a test should
#: not have to know about -- but the mapping has to be RIGHT, and one of these
#: was not. `operator_trace` pointed at `"trace"`, which is not a kernel module
#: at all: there is no `operator_kernel/trace.py`, so `__import__("trace")`
#: returned the STANDARD LIBRARY's tracing module and bound its contents into
#: this namespace. `op.operator_trace.trace_path` was an AttributeError on
#: Python's coverage tracer, `op.main` silently resolved to `trace.main`, and
#: every public name in stdlib `trace` was registered as a write target, so
#: `monkeypatch.setattr(op, "time", ...)` reached into it. The behaviour those
#: tests want is in `evidence.py` -- `trace_path` and `ancestry` are both
#: defined there. `_bind` now refuses a non-kernel module outright, so the next
#: wrong entry here is an error at import rather than a wrong answer later.
_ALIASES = {
    "operator_liveness": "process_identity",
    "install_manifest": "presence",
    "operator_trace": "evidence",
    "work_claims": "claims",
}


KERNEL = Path(__file__).resolve().parent.parent / "operator_kernel"


def is_kernel_module(module) -> bool:
    """Does ``module`` actually live in this repository's kernel?

    A separate, callable predicate rather than an inline check inside
    :meth:`_KernelNamespace._bind`, so that a test can hand it the module that
    got through -- the standard library's ``trace`` -- and watch it be refused.
    Inline, the only available control was "stdlib ``trace`` is not under
    ``operator_kernel/``", which is a true statement about the filesystem that
    holds whether or not the predicate exists at all.
    """
    origin = getattr(module, "__file__", None)
    if origin is None:
        return False
    try:
        return KERNEL in Path(origin).resolve().parents
    except OSError:
        return False


class _KernelNamespace(types.ModuleType):
    """One namespace over many modules, with writes forwarded to the owner."""

    def __init__(self, name):
        super().__init__(name)
        self.__dict__["_owner"] = {}

    def _bind(self):
        holders = self.__dict__["_owner"]
        for mod_name in _MODULE_NAMES:
            module = __import__(mod_name)
            # A name in `_MODULE_NAMES` that is not a kernel module does not
            # fail -- it imports something ELSE and binds its contents here
            # under kernel-looking names. `"trace"` did precisely that for the
            # life of the extraction. Resolving to the wrong file is the
            # failure this repository has now hit four times, so it is checked
            # rather than assumed.
            if not is_kernel_module(module):
                raise ImportError(
                    f"tests/op.py names {mod_name!r} as a kernel module, but it "
                    f"resolves to {getattr(module, '__file__', None)!r}, which "
                    f"is not under {KERNEL}. Binding it would put another "
                    f"package's names into the kernel namespace under kernel "
                    f"spellings."
                )
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
_ns.__dict__["KERNEL"] = KERNEL
_ns.__dict__["is_kernel_module"] = is_kernel_module
_ns.__dict__["Path"] = Path
_ns.__dict__["sys"] = sys
_ns._bind()
sys.modules[__name__] = _ns
