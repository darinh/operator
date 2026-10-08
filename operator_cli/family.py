"""Who is running a command, and what that caller may do to which operator.

A person may act on any operator. An operator, meaning the agent in its
session, may act only on the operators it started, and only within the caps.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path


def caller(verb: str):
    """The agent or person running this command, or None after saying why not."""
    from custody import caller as classify
    who = classify(os.getpid())
    if isinstance(who, str):
        print(f"operator {verb}: {who}", file=sys.stderr)
        return None
    return who


def lineage_of(who) -> tuple:
    """(parent, started_by_pid) for an operator ``who`` is starting."""
    import operators
    from custody import Agent
    if isinstance(who, Agent):
        return who.record.id, who.copilot_pid
    return operators.HUMAN, who.shell_pid


def refuse(verb: str, why: str) -> int:
    print(f"operator {verb}: {why}", file=sys.stderr)
    return 2


def not_yours(verb: str, who, record) -> bool:
    """True, after saying why, when ``who`` may not act on ``record``."""
    import lineage
    import operators
    if lineage.may_manage(lineage_of(who)[0], record.id, operators.all_operators() or []):
        return False
    refuse(verb, f"{record.name} is not your child. "
                 f"An operator may {verb} only the operators it started.")
    return True


def person_only(verb: str) -> int:
    """0 when a person runs this command, else the exit code after saying why."""
    from custody import Agent
    who = caller(verb)
    if who is None:
        return 1
    if isinstance(who, Agent):
        return refuse(verb, "only a person can do this, not an operator")
    return 0


def cap_problem(who, new: bool) -> "str | None":
    """Why ``who`` may not run one more child, or None. A person has no cap."""
    import config
    import lineage
    import operators
    from custody import Agent
    from supervisor_control import active_instances
    if not isinstance(who, Agent):
        return None
    agent = who
    records = operators.all_operators() or []
    live = {inst.id for inst in active_instances()}
    running = [op for op in lineage.children(agent.record.id, records) if op.id in live]
    if len(running) >= config.max_children():
        return (f"{agent.record.name} already runs {len(running)} children, and "
                f"OPERATOR_MAX_CHILDREN allows {config.max_children()}. Stop one first.")
    level = lineage.depth(agent.record.id, records) + 1
    if new and level > config.max_depth():
        return (f"a child of {agent.record.name} would be {level} levels deep, and "
                f"OPERATOR_MAX_DEPTH allows {config.max_depth()}.")
    return None


def place(who, name, attach_now: bool, where) -> "Path | int":
    """Where ``who`` starts an operator, or the exit code after saying why not.

    A person starts in the directory they stand in. An agent starts its child
    where its own operator works, not wherever its shell has wandered to.
    """
    from custody import Agent
    agent = isinstance(who, Agent)
    if agent and name is None:
        return refuse("start", 'an operator must name the child it starts: '
                               'operator start NAME "..."')
    if agent and attach_now:
        return refuse("start", "only a person can attach, so an operator "
                               "cannot pass --attach")
    base = Path(who.record.cwd) if agent else Path.cwd()
    if where is None:
        return base
    there = Path(where).resolve()
    problem = dir_problem(there, base)
    return refuse("start", problem) if problem else there


def dir_problem(path: Path, base: Path) -> "str | None":
    """Why ``path`` is not a checkout of ``base``'s repository, or None."""
    import paths
    roots = paths.worktree_roots(base)
    if roots is None:
        return f"{base} is not in a git repository, so it has no worktrees to start in."
    if any(paths.catalog_paths_match(path, str(root)) is True for root in roots):
        return None
    return (f"{path} is not a worktree of this project.\n"
            "Create it first, then run this command again:\n"
            f'    git -C "{base}" worktree add "{path}"')
