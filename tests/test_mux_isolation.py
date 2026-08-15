"""The multiplexer guard, graded rather than assumed.

`conftest._no_real_multiplexer` has two halves and they fail differently. The
spawn poison fails loudly -- a test that reaches a real client gets an
AssertionError naming its own nodeid. The substitution fails *silently*: if it
writes to something the kernel does not read, every test still passes and every
`has_session` goes to the developer's live server.

That is not hypothetical. The substitution arrived in this repository with the
extraction as `copilot_operator.MUX = FakeMux()` -- the attribute on the module
this kernel was extracted from, which lives in a different repository and which
`tests/test_kernel_boundary.py` names on `FORBIDDEN`. It was inert for the whole
life of the extraction, across 289 passing tests, and nothing could have gone
red: an assignment to an unrelated module is not an error.

So the guard needs positive controls, and they have to grade the thing that
stays quiet. `test_the_substitution_reaches_every_kernel_module_that_reads_it`
is the one that would have caught it on day one.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

import mux
import op
from conftest import FakeMux, _is_a_multiplexer_spawn

REPO = Path(__file__).resolve().parent.parent
KERNEL = REPO / "operator_kernel"


# ── the substitution half ───────────────────────────────────────
def _kernel_modules_binding_mux() -> set[str]:
    """Kernel modules that hold a module-level `MUX`, read off the source.

    Read from disk rather than from the imported modules, because the failure
    being guarded against is a module the substitution never reached -- and an
    unreached module is exactly one whose *runtime* attribute would be checked
    against itself. Source is the independent measurement.

    Both spellings count. `config` defines `MUX`; the readers say
    `from config import MUX`, which binds a second name that a write to
    `config` alone does not touch.
    """
    holders: set[str] = set()
    for path in sorted(KERNEL.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:
            if isinstance(node, ast.ImportFrom):
                if any(alias.name == "MUX" for alias in node.names):
                    holders.add(path.stem)
            elif isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id == "MUX":
                        holders.add(path.stem)
    return holders


def test_there_are_kernel_modules_holding_mux_to_check():
    """Without this, every assertion below passes over an empty set.

    A coverage test whose subject list is empty is the most confident-looking
    way to check nothing.
    """
    assert len(_kernel_modules_binding_mux()) >= 2


def test_the_substitution_reaches_every_kernel_module_that_reads_it():
    """The one that would have caught the inert guard.

    Seven kernel modules do `from config import MUX` and hold their own
    reference, so a write to `config.MUX` alone leaves six of them pointed at
    the developer's live tmux server -- and `launch.py` is one of the six, which
    is the module that calls `new_session` and `kill_session`.
    """
    unreached = []
    for name in sorted(_kernel_modules_binding_mux()):
        module = __import__(name)
        seen = getattr(module, "MUX", None)
        if not isinstance(seen, FakeMux):
            unreached.append(f"{name}.MUX is {type(seen).__name__}")
    assert unreached == [], (
        "the autouse multiplexer substitution did not reach these modules:\n  "
        + "\n  ".join(unreached)
        + "\n\nThey will call has_session/kill_session against this machine's "
        "real multiplexer. Whatever conftest writes to must forward to every "
        "module binding the name -- that is what `op.MUX = ...` is for."
    )


def test_the_op_namespace_forwards_to_every_module_binding_mux():
    """The mechanism, checked separately from its effect.

    The test above would still pass if `op` reached the modules by luck --
    say, because they all happened to be imported after the write. This one
    grades the forwarding map itself, so a kernel module added tomorrow that
    binds `MUX` and is missing from `op._MODULE_NAMES` fails here with its own
    name in the message rather than by leaking at some later date.
    """
    forwarded = {module.__name__ for module in op.holders_of("MUX")}
    missing = sorted(_kernel_modules_binding_mux() - forwarded)
    assert missing == [], (
        f"these modules bind MUX but `op` does not forward writes to them: "
        f"{missing}. Add them to `_MODULE_NAMES` in tests/op.py -- until then "
        f"conftest's substitution silently skips them."
    )


def test_every_holder_sees_the_same_multiplexer():
    """Partial substitution and partial restoration look identical from here.

    Either one leaves the kernel disagreeing with itself about which server it
    is talking to, which is worse than being uniformly wrong: `launch` creates
    the session on one and `session_state` asks the other whether it exists.
    """
    seen = {module.__name__: getattr(module, "MUX", None)
            for module in op.holders_of("MUX")}
    distinct = {id(value) for value in seen.values()}
    assert len(distinct) == 1, (
        "kernel modules hold different multiplexers: "
        + ", ".join(f"{name}={type(value).__name__}@{id(value)}"
                    for name, value in sorted(seen.items()))
    )


#: Every fake this module has been handed, kept alive on purpose. Identity is
#: the question being asked, and `id()` is reused once an object is collected,
#: so comparing bare ids would eventually compare a live fake against a dead
#: one's address and fail for a reason that has nothing to do with isolation.
_MULTIPLEXERS_SEEN: list = []


def _record_a_fresh_multiplexer():
    """Assert this test's fake is new and empty, then remember it.

    Written as a helper each test calls, rather than as an assertion in a
    second test about what a first test did, and the difference is the whole
    point. The earlier version of this pair asserted `"leak-detector" not in
    op.MUX.sessions` in a test that depended on its predecessor having put it
    there -- so under `-k`, under `pytest-xdist`, or under any randomised
    ordering, the second test ran against an empty table and passed without
    grading anything. A test that passes for the wrong reason when run alone is
    the shape this repository is least able to afford.

    Each caller now grades itself: the table it was handed is empty, and the
    object is not one any earlier test in this process was handed. Run alone,
    the emptiness assertion still bites; run together, so does the identity one.
    """
    assert op.MUX.sessions == {}, (
        f"this test began with sessions already present: {op.MUX.sessions}. "
        f"The fake is being shared between tests rather than rebuilt."
    )
    assert not any(seen is op.MUX for seen in _MULTIPLEXERS_SEEN), (
        "this test was handed a multiplexer an earlier test already used; the "
        "autouse fixture is not building a new one per test"
    )
    _MULTIPLEXERS_SEEN.append(op.MUX)


def test_a_test_gets_its_own_multiplexer_not_a_shared_one():
    """State written by one test must not be readable by the next.

    The fake is a dict, so a single shared instance would carry sessions across
    tests -- the same order-dependence the real server produced, moved
    in-process where it is harder to see.
    """
    _record_a_fresh_multiplexer()
    op.MUX.new_session("leak-detector", str(REPO), ["true"])
    assert "leak-detector" in op.MUX.sessions


def test_a_second_test_is_handed_a_different_multiplexer_again():
    """Paired with the test above, but no longer dependent on it.

    It asserts the same self-contained property, so running it alone grades
    something rather than passing on an empty table it never expected to
    contain anything.
    """
    _record_a_fresh_multiplexer()
    assert "leak-detector" not in op.MUX.sessions


# ── the spawn-poison half ───────────────────────────────────────
#
# These are the three positive controls the conftest docstring refers to. They
# assert the guard *destroys nothing*, and getting them wrong is expensive:
# `Mux(binary="tmux")._run("kill-server")` unguarded is a real kill-server, and
# on this machine that was measured at seven live sessions -- six peer agents
# and the reviewing session itself.
def test_the_guard_refuses_a_real_multiplexer_spawn():
    with pytest.raises(AssertionError) as excinfo:
        mux.Mux(binary="tmux")._run("has-session", "-t", "anything")
    assert "real terminal multiplexer" in str(excinfo.value)


def test_the_refusal_names_the_test_and_the_argv():
    """A refusal nobody can trace is a refusal somebody disables."""
    with pytest.raises(AssertionError) as excinfo:
        mux.Mux(binary="tmux")._run("kill-server")
    message = str(excinfo.value)
    assert "test_the_refusal_names_the_test_and_the_argv" in message
    assert "kill-server" in message


def test_the_guard_delegates_every_other_subprocess():
    """Over-refusing would break the suite's real Python child processes."""
    import subprocess
    import sys

    done = subprocess.run([sys.executable, "-c", "print('ok')"],
                          capture_output=True, text=True)
    assert done.returncode == 0
    assert done.stdout.strip() == "ok"


@pytest.mark.parametrize("argv", [
    ["tmux"], ["psmux"], ["pmux"], ["tmux.exe"], ["TMUX.EXE"],
    [r"C:\tools\tmux.exe"], ["/usr/bin/tmux"], [r"C:tmux.exe"],
    ["tmux", "kill-server"], [r"C:\Program Files\tmux.exe"],
    # The shapes the predicate used to miss. `subprocess.run` accepts a command
    # LINE as a string -- with shell=True anywhere, and without it on Windows,
    # where it goes to CreateProcess unsplit. Both were answered False, and
    # `"tmux kill-server"` is the single most destructive argv this file names.
    "tmux", "tmux kill-server", "/usr/bin/tmux attach", r"C:tmux.exe kill-server",
    '"C:\\Program Files\\tmux.exe" kill-server',
    b"tmux", b"tmux kill-server", [b"tmux"],
    Path("tmux"), [Path(r"C:\tools\tmux.exe")],
])
def test_the_spawn_predicate_recognises_a_multiplexer(argv):
    """Windows and POSIX spellings both, on whichever platform is running.

    The predicate is asked about argv naming the *other* platform's syntax --
    a POSIX runner still has to recognise `C:\\tools\\tmux.exe` -- so it must
    not be tested only in the spelling native to the machine at hand.
    """
    assert _is_a_multiplexer_spawn(argv) is True


@pytest.mark.parametrize("argv", [
    ["python"], ["git", "status"], ["tmuxinator"], ["notmux"], ["copilot"],
    "python -c pass", "tmuxinator start", "notmux", b"python", "", [],
    ["python", "tmux.py"], "python tmux.py",
])
def test_the_spawn_predicate_leaves_everything_else_alone(argv):
    """Negative control: a predicate that refuses everything also 'passes'.

    `["python", "tmux.py"]` and its string form are the cases that matter most
    here: the guard must read the *program*, not any argument that happens to
    name a multiplexer, or the suite loses the ability to run child processes.
    """
    assert _is_a_multiplexer_spawn(argv) is False
