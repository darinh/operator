"""What the reference extensions are allowed to be.

Four properties, each of which the extension system's design states somewhere
and none of which anything checked until this file existed.

**They must be discoverable exactly as `pyproject.toml` says.** An entry point
whose target does not import, or imports but implements no hook, is an
extension that is registered and silent -- and a silent extension is
indistinguishable from a working one that had nothing to say, which is this
project's oldest failure. The registrations are read from the packaging
metadata rather than from a list kept here, because a list kept here would
agree with itself while `pyproject.toml` drifted.

**They must not import the kernel.** An extension is third-party code by
definition. One that reaches into `config.py` for a constant is demonstrating a
coupling no installed package can have, and a reference implementation that
does it is teaching everybody who copies it to do it too.

**They must not be able to mutate a repository.** `gitfacts` is the only place
in the package that may start a process, and every git command it constructs is
checked here against an allowlist -- over the parsed source, not over the paths
these tests happen to reach. A janitor that could delete the worktree it is
about to propose removing is the whole propose-never-dispose design inverted,
and the directory it deletes may hold somebody's uncommitted afternoon.

**They must ship inert.** Registering an entry point is enough to be asked a
question on the launch path of every seat, and `admit_launch` refusals are
honoured. Every hook of every extension is asked here with no configuration
present, and every one of them has to have no opinion.
"""
from __future__ import annotations

import ast
import importlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

import extensions
import fleet_host
from operator_extensions import activation
from test_kernel_boundary import (ALLOWED_THIRD_PARTY, FORBIDDEN,
                                  MAX_MODULE_CODE_LINES, MAX_MODULE_LINES,
                                  REPO, code_lines, imported_names)

EXTENSIONS = REPO / "operator_extensions"
CLI = REPO / "operator_cli"

#: Budgets for the two packages this commit created. They exist for the reason
#: `test_fleet_boundary.py` gives at length: a cut into an unguarded directory
#: is not a cut, it is a place to put things where nothing counts them, and
#: every rule the kernel is held to would then be one `git mv` away from not
#: applying. Two new top-level packages with no ceilings would be exactly that
#: hole, opened on the same day the last one was closed.
#:
#: Set at the measured size plus room for the next few extensions -- generous
#: for what exists, binding well before either becomes a second god module.
#: `operator_cli` gets the smaller one on purpose: it is meant to stay a thin
#: entry point, and anything with a decision in it belongs a layer down where
#: the kernel's budgets and boundary tests already apply.
MAX_EXTENSION_CODE_LINES = 700
MAX_EXTENSION_TOTAL_LINES = 1600
#: Raised from 300, which the package was exactly at, when `supervise` and
#: `recover` were added. Both are the shape this budget asks for -- parse
#: arguments, call in, decide nothing -- and the per-module ceilings that stop
#: any one of them growing a brain are unchanged. Re-set at the measured size
#: plus room for the next few commands, which is how the number was set in the
#: first place.
#:
#: Raised again from 450 / 700 when `operator` itself became a console script.
#: A numbered menu plus the verbs it dispatches cannot fit in the leftover
#: 105 / 89 lines, and deleting the existing command docstrings to make room
#: would be the edit `test_kernel_boundary.py` names as the most damaging
#: available. Re-set at the measured size plus room for a couple of verbs.
#: The per-module ceilings are unchanged; `entry.py` is still well under them.
MAX_CLI_CODE_LINES = 850
MAX_CLI_TOTAL_LINES = 1200

#: The two closed hook sets, unioned. An extension may implement hooks from
#: either host; no *host* will ask it something outside its own set, which is
#: what `Host(hooks=...)` exists to guarantee.
ALL_HOOKS = frozenset(extensions.HOOKS) | frozenset(fleet_host.FLEET_HOOKS)

#: The git verbs `gitfacts` may use. Everything absent from this set is either
#: a mutation or a network call, and neither belongs in a hook that answers a
#: question on the launch path.
READ_ONLY_VERBS = frozenset({"rev-parse", "worktree", "branch", "status",
                             "ls-files"})

#: Tokens that mutate, in any position. `worktree` is read-only as `worktree
#: list` and destructive as `worktree remove`, so the verb allowlist above is
#: not sufficient on its own and this is the half that catches the subcommand.
MUTATING_TOKENS = frozenset({
    "add", "remove", "prune", "lock", "unlock", "move", "repair",
    "commit", "checkout", "reset", "clean", "stash", "restore", "rm", "mv",
    "push", "pull", "fetch", "merge", "rebase", "cherry-pick", "revert",
    "-d", "-D", "-f", "--force", "--delete", "--prune",
})


def extension_modules() -> "list[Path]":
    return sorted(p for p in EXTENSIONS.glob("*.py") if p.stem != "__init__")


def cli_modules() -> "list[Path]":
    return sorted(p for p in CLI.glob("*.py") if p.stem != "__init__")


def declared_entry_points() -> "dict[str, str]":
    """`{name: target}` from `pyproject.toml`'s extension entry-point table.

    Parsed from the file rather than from `importlib.metadata`, because the
    package is not necessarily installed in the environment running the suite
    -- and a test that quietly checks nothing when the package is absent is
    worse than no test. `test_the_entry_point_table_was_actually_found` is what
    stops this returning an empty dict and every case below passing vacuously.

    Hand-parsed rather than read with `tomllib`, for the reason
    `test_fleet_boundary._declared_list` already gives: `tomllib` arrived in
    3.11 and this repository claims `>=3.10`. A `try: import tomllib / except:
    fall back` looks like it honours that and does not. The name is still an
    import statement, so on 3.10 the boundary scan reads it as the suite
    reaching outside the repository, and the fallback branch it guards was the
    one never exercised on the machine anyone develops on.
    """
    text = (REPO / "pyproject.toml").read_text(encoding="utf-8")
    header = f'[project.entry-points."{extensions.ENTRY_POINT_GROUP}"]'
    found: dict[str, str] = {}
    inside = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped == header:
            inside = True
            continue
        if inside and stripped.startswith("["):
            break
        if inside and "=" in stripped and not stripped.startswith("#"):
            name, _, target = stripped.partition("=")
            found[name.strip()] = target.strip().strip('"')
    return found


def test_the_entry_point_table_was_actually_found():
    """The control for every case below that iterates the table.

    A parser that returns nothing turns each of those into a loop over an empty
    sequence, which passes. Unfalsifiable by its input is the defect; this is
    the input being pinned.
    """
    declared = declared_entry_points()
    assert len(declared) == 3, (
        f"expected three registered extensions, parsed {declared!r} from "
        f"pyproject.toml -- if an extension was added or removed, this number "
        f"is the decision to update")
    assert set(declared) == {"worktree-guard", "worktree-janitor",
                             "seat-watch"}


@pytest.mark.parametrize("name,target", sorted(declared_entry_points().items()))
def test_every_registration_imports_and_implements_a_hook(name, target):
    module = importlib.import_module(target)
    implemented = {hook for hook in ALL_HOOKS
                   if callable(getattr(module, hook, None))}
    assert implemented, (
        f"{name} registers {target}, which implements none of {sorted(ALL_HOOKS)}. "
        f"`extension_worker` replies `implemented: false` and the extension is "
        f"registered and silent.")


@pytest.mark.parametrize("name,target", sorted(declared_entry_points().items()))
def test_every_registration_survives_the_kernels_own_name_rules(name, target):
    """`discover` refuses these, so a registration that trips one is inert."""
    assert extensions._NAME_RE.fullmatch(name), (
        f"{name!r} is not a name `discover` will accept")
    assert extensions._TARGET_RE.fullmatch(target), (
        f"{target!r} is not a target `discover` will accept")
    assert extensions._name_grants(name) == [], (
        f"{name!r} reads as a grant of authority; `discover` refuses it and "
        f"the extension would never be asked anything")


@pytest.mark.parametrize("name,target", sorted(declared_entry_points().items()))
def test_no_extension_implements_a_hook_outside_the_closed_sets(name, target):
    """A function named like a hook that no host asks is dead code that reads
    as live code."""
    module = importlib.import_module(target)
    hookish = {attr for attr in vars(module)
               if attr.startswith(("on_", "admit_", "gate_", "detect_",
                                   "propose_"))
               and callable(getattr(module, attr))}
    assert hookish <= ALL_HOOKS, (
        f"{name} defines {sorted(hookish - ALL_HOOKS)}, which no host asks")


def test_discovery_accepts_the_registrations_as_written():
    """The real `discover`, fed the real table, in the shape metadata gives it."""
    class Fake:
        def __init__(self, name, value):
            self.name, self.value = name, value

    declared = declared_entry_points()
    found, failures = extensions.discover(
        [Fake(name, target) for name, target in declared.items()])
    assert failures == []
    assert {e.name for e in found} == set(declared)


# ── what the package may contain ────────────────────────────────

@pytest.mark.parametrize("path", extension_modules() + cli_modules(),
                         ids=lambda p: p.name)
def test_no_module_here_imports_a_forbidden_one(path):
    assert not (imported_names(path.read_text(encoding="utf-8")) & FORBIDDEN)


@pytest.mark.parametrize("path", extension_modules(), ids=lambda p: p.name)
def test_an_extension_imports_only_the_standard_library_and_itself(path):
    """Third-party by definition, and that includes being third party to this
    repository's kernel."""
    imported = imported_names(path.read_text(encoding="utf-8"))
    kernel_names = {p.stem for p in (REPO / "operator_kernel").glob("*.py")}
    fleet_names = {p.stem for p in (REPO / "operator_fleet").glob("*.py")}
    reached = imported & (kernel_names | fleet_names)
    assert reached == set(), (
        f"{path.name} imports {sorted(reached)} from this repository's "
        f"packages. No installed extension could do that, so a reference "
        f"extension must not either.")
    outside = imported - set(sys.stdlib_module_names) - ALLOWED_THIRD_PARTY
    outside -= {"operator_extensions"}
    assert outside == set(), f"{path.name} imports {sorted(outside)}"


def test_only_gitfacts_may_start_a_process():
    """One chokepoint, so the allowlist below is the whole story."""
    offenders = [path.name for path in extension_modules()
                 if path.stem != "gitfacts"
                 and "subprocess" in imported_names(
                     path.read_text(encoding="utf-8"))]
    assert offenders == [], (
        f"{offenders} can start a process without going through gitfacts, so "
        f"the read-only allowlist no longer covers the package")


def git_command_literals() -> "list[list[str]]":
    """Every literal argument list handed to `gitfacts._run`, from the AST."""
    tree = ast.parse((EXTENSIONS / "gitfacts.py").read_text(encoding="utf-8"))
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        target = node.func
        name = getattr(target, "attr", None) or getattr(target, "id", None)
        if name != "_run" or len(node.args) < 2:
            continue
        argv = node.args[1]
        if not isinstance(argv, ast.List):
            continue
        literals = [element.value for element in argv.elts
                    if isinstance(element, ast.Constant)
                    and isinstance(element.value, str)]
        if literals:
            found.append(literals)
    return found


def test_the_git_command_scan_finds_the_commands():
    """Control: an AST walk that matches nothing would pass every case below."""
    commands = git_command_literals()
    assert len(commands) >= 5, (
        f"only found {commands!r}; the scan below is not reading gitfacts")
    assert ["status", "--porcelain", "--untracked-files=all"] in commands, (
        "the working-tree check must ask about untracked files explicitly; "
        "`status.showUntrackedFiles=no` empties the default output")


@pytest.mark.parametrize("argv", git_command_literals(),
                         ids=lambda a: " ".join(a))
def test_every_git_command_is_read_only(argv):
    assert argv[0] in READ_ONLY_VERBS, (
        f"{argv[0]!r} is not an allowed git verb")
    offending = [token for token in argv if token in MUTATING_TOKENS]
    assert offending == [], (
        f"`git {' '.join(argv)}` contains {offending}, which mutates. This "
        f"package proposes; the human disposes.")


def test_the_mutating_token_scan_would_catch_a_mutation():
    """Positive control for the check above, which is otherwise a loop that
    could be asserting nothing."""
    assert [t for t in ["worktree", "remove", "--force"]
            if t in MUTATING_TOKENS] == ["remove", "--force"]


# ── inert until a human says otherwise ──────────────────────────

@pytest.mark.parametrize("name,target", sorted(declared_entry_points().items()))
def test_every_hook_has_no_opinion_when_nothing_is_configured(
        name, target, tmp_path, monkeypatch):
    """A sweep: no hook of any extension may answer with an empty home.

    Necessary and *not sufficient*, which is why the paired test below exists.
    A reviewer pointed out that this on its own is unfalsifiable for
    `worktree-guard`: the `workdir` handed over is an empty temporary
    directory, so the guard returns None because it is not a repository, and an
    extension that had forgotten to consult `activation` entirely would pass
    here unchanged.
    """
    monkeypatch.setenv("COPILOT_OPERATOR_HOME", str(tmp_path / "empty-home"))
    module = importlib.import_module(target)
    asked = 0
    for hook in sorted(ALL_HOOKS):
        fn = getattr(module, hook, None)
        if not callable(fn):
            continue
        asked += 1
        assert fn(instance="seat", session=1, workdir=str(tmp_path),
                  facts=[], now="2026-08-17T12:00:00Z", elapsed=1.0) is None, (
            f"{name}.{hook} answered without being switched on")
    assert asked, f"{name} was not actually asked anything"


def _git(root, *args):
    done = subprocess.run(["git", "-C", str(root), *args],
                          capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr


def _repo(tmp_path, name):
    root = tmp_path / name
    root.mkdir()
    _git(root, "init", "-b", "main")
    _git(root, "config", "user.email", "t@example.invalid")
    _git(root, "config", "user.name", "T")
    _git(root, "config", "commit.gpgsign", "false")
    (root / "f.txt").write_text("x\n", encoding="utf-8")
    _git(root, "add", "f.txt")
    _git(root, "commit", "-m", "base")
    return root


def _guard_scenario(tmp_path):
    """A repository genuinely part-way through a merge."""
    root = _repo(tmp_path, "guard")
    marker = Path(subprocess.run(
        ["git", "-C", str(root), "rev-parse", "--absolute-git-dir"],
        capture_output=True, text=True, timeout=60).stdout.strip())
    (marker / "MERGE_HEAD").write_text("x\n", encoding="utf-8")
    return ({"enabled": True},
            [("admit_launch", {"instance": "seat", "session": 1,
                               "workdir": str(root)})])


def _janitor_scenario(tmp_path):
    """A repository with a linked worktree whose branch `main` contains."""
    root = _repo(tmp_path, "janitor")
    _git(root, "worktree", "add", "-b", "landed",
         str(root / ".worktrees" / "landed"))
    return ({"enabled": True, "roots": [str(root)], "integration": "main"},
            [("propose_work", {})])


def _seat_watch_scenario(tmp_path):
    return ({"enabled": True, "failures": 1},
            [("on_fact", {"facts": [{"ts": "2026-08-17T10:00:00Z",
                                     "event": "session_exit",
                                     "instance": "alpha", "consecutive": 4,
                                     "giving_up": False}]}),
             ("propose_work", {})])


#: One scenario per registered extension, each of which *would* produce an
#: answer if the extension were switched on. A `KeyError` here is the intended
#: behaviour for a fourth extension: a new one must come with the inputs that
#: prove it is inert, or this file cannot prove it for it.
SCENARIOS = {
    "worktree-guard": _guard_scenario,
    "worktree-janitor": _janitor_scenario,
    "seat-watch": _seat_watch_scenario,
}


@pytest.mark.parametrize("name", sorted(declared_entry_points()))
def test_each_extension_is_inert_without_config_and_answers_with_it(
        name, tmp_path, monkeypatch):
    """The paired test, and the one that can actually fail.

    The same inputs are put to the extension twice: once with no configuration
    and once with it enabled. Inert-then-silent proves nothing, because a
    scenario that never triggers the logic is silent either way. Inert-then-
    *answering* proves the input reaches the logic, and therefore that the
    silence in the first half was `activation` and not the scenario.
    """
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("COPILOT_OPERATOR_HOME", str(home))
    config, calls = SCENARIOS[name](tmp_path)
    module = importlib.import_module(declared_entry_points()[name])

    for hook, kwargs in calls:
        assert getattr(module, hook)(**kwargs) is None, (
            f"{name}.{hook} answered with no configuration present")

    (home / activation.CONFIG_NAME).write_text(
        json.dumps({name: config}), encoding="utf-8")
    answers = [getattr(module, hook)(**kwargs) for hook, kwargs in calls]
    assert answers[-1] is not None, (
        f"positive control failed: {name} did not answer even when enabled, "
        f"so the first half of this test proved nothing about activation")


@pytest.mark.parametrize("path", extension_modules() + cli_modules(),
                         ids=lambda p: p.name)
def test_nothing_here_shadows_a_standard_library_module(path):
    assert path.stem not in sys.stdlib_module_names


@pytest.mark.parametrize("package", ["operator_extensions", "operator_cli"])
def test_the_new_package_names_do_not_collide_with_an_installed_one(package):
    """`copilot-tools` is installed editable on the development machine and
    owns a shelf of `operator_*` names. Colliding with one made seventy tests
    pass against the system being replaced."""
    module = importlib.import_module(package)
    origin = Path(getattr(module, "__file__", "") or "").resolve()
    assert REPO in origin.parents, (
        f"{package} resolves to {origin}, which is not in this repository")


# ── budgets ─────────────────────────────────────────────────────

def _measure(paths) -> "tuple[int, int]":
    sources = [path.read_text(encoding="utf-8") for path in paths]
    return (sum(code_lines(s) for s in sources),
            sum(len(s.splitlines()) for s in sources))


def test_the_budget_scan_actually_sees_both_packages():
    """Control. A glob that stops matching turns every ceiling below into
    `0 <= budget`, which passes while measuring nothing."""
    assert len(extension_modules()) >= 4
    assert len(cli_modules()) >= 1
    assert _measure(extension_modules())[0] > 100


def test_the_extensions_package_stays_under_its_budget():
    code, total = _measure(extension_modules())
    assert code <= MAX_EXTENSION_CODE_LINES, (
        f"operator_extensions is {code} code lines, budget "
        f"{MAX_EXTENSION_CODE_LINES}. A fourth extension is a decision about "
        f"what ships as a reference, not a formality -- most extensions should "
        f"live in their own distribution.")
    assert total <= MAX_EXTENSION_TOTAL_LINES


def test_the_cli_package_stays_under_its_budget():
    code, total = _measure(cli_modules())
    assert code <= MAX_CLI_CODE_LINES, (
        f"operator_cli is {code} code lines, budget {MAX_CLI_CODE_LINES}. "
        f"This package parses arguments and calls in; anything with a decision "
        f"in it belongs where the kernel's boundary tests already reach.")
    assert total <= MAX_CLI_TOTAL_LINES


@pytest.mark.parametrize("path", extension_modules() + cli_modules(),
                         ids=lambda p: p.name)
def test_no_module_here_exceeds_the_per_module_ceilings(path):
    source = path.read_text(encoding="utf-8")
    assert code_lines(source) <= MAX_MODULE_CODE_LINES
    assert len(source.splitlines()) <= MAX_MODULE_LINES
