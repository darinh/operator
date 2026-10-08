# Child operators

An agent inside an operator's session runs `operator start NAME "task"`, and the new operator is its child. It starts in the parent's directory, or in another worktree of the same project with `--dir`. `operator list` draws it under its parent. An agent may start, stop and delete only its own children, within a cap on live children and on depth. It may not attach, rename or recover. Stopping a parent stops everything under it. Deleting a parent leaves its children, which then list at the top.

## Sub-features

- `child-start` an agent's `start NAME` records the caller as parent and starts in the parent's directory.
- `child-tree` `list` indents a child under its parent and says `child of NAME`.
- `child-name-required` an agent's `start` with no name refuses.
- `child-no-attach` an agent's `start --attach` refuses.
- `child-dir` `--dir PATH` starts in a worktree of the same project, and otherwise prints the `git worktree add` command.
- `cap-children` a parent runs at most `OPERATOR_MAX_CHILDREN` children, 4 by default.
- `cap-depth` operators nest at most `OPERATOR_MAX_DEPTH` deep, 3 by default, counting the one a person started.
- `scope` an agent cannot stop its parent, a sibling, or anything that is not its child.
- `person-only` an agent's `attach`, `rename` and `recover NAME` refuse.
- `cascade-stop` `stop PARENT` stops the whole subtree and names each one.
- `rehome` deleting a parent leaves its children at the top level.

## How to get to it (user POV)

- An agent runs `operator start NAME "task"`, `operator start NAME --dir PATH "task"`, `operator stop NAME` and `operator delete NAME --yes` from its session. Every launch preamble names its parent, its children, and the start, `--dir` and stop commands. It does not mention delete.
- A person sees the tree in `operator list` and in the menu's List operators. A person's `operator stop NAME` cascades, as Stop in the menu does.
- `--dir` is only on the command line. The menu starts an operator where you stand.

## Driving it with control_operator

Preconditions:

- `up` has run and `doctor --run <run>` printed `doctor: healthy`.
- Every agent step below is written with `agent` before the operator that runs it starts.
- To exercise the caps cheaply, set `OPERATOR_MAX_CHILDREN=2` and `OPERATOR_MAX_DEPTH=2` in the shell that runs `operator -- start lead`. The supervisor passes the environment down to every session, and so to every agent caller.

- **Script the parent.** `agent --run <run> lead` with these steps, in order:
  `{"op":["start"]}`, `{"op":["start","scout","--attach"]}`, `{"op":["start","scout","role=scout"]}`, `{"op":["start","wt","--dir",".worktrees/wt","role=wt"]}`, `{"op":["attach","scout"]}`, `{"op":["rename","scout","renamed"]}`, `{"op":["recover","scout"]}`, `{"on":"retry","do":[{"op":["start","wt","--dir",".worktrees/wt","role=wt"]},{"op":["start","third","role=third"]}]}`.
- **Script the child.** `agent --run <run> scout` with `{"op":["stop","lead"]}` and `{"op":["start","deep","role=deep"]}`.
- **Start the parent as a person.** `operator --run <run> -- start lead "role=lead"`, then `wait --run <run> --file artifacts/agents/scout/commands.log --contains OPERATOR_MAX_DEPTH --timeout 60`.
- **Read the parent's refusals** in `artifacts/agents/lead/commands.log`. Each line here is the command, then its exit and output.
  - `operator start`: exit 2, `operator start: an operator must name the child it starts: operator start NAME "..."`.
  - `operator start scout --attach`: exit 2, `operator start: only a person can attach, so an operator cannot pass --attach`.
  - `operator start scout role=scout`: exit 0, `started scout (pid N)`.
  - `operator start wt --dir .worktrees/wt role=wt`: exit 2. It prints `<run>\repo\.worktrees\wt is not a worktree of this project.`, then `Create it first, then run this command again:`, then the exact `git -C "<run>\repo" worktree add "<run>\repo\.worktrees\wt"`.
  - `attach`, `rename` and `recover`: exit 2 each, `operator VERB: only a person can do this, not an operator`.
- **Read the child's refusals** in `artifacts/agents/scout/commands.log`.
  - `operator stop lead`: exit 2, `operator stop: lead is not your child. An operator may stop only the operators it started.`
  - `operator start deep role=deep`: exit 2, `a child of scout would be 3 levels deep, and OPERATOR_MAX_DEPTH allows 2.`
- **Make the worktree, then let the parent retry.** `git -C <run>\repo worktree add .worktrees/wt`, then `operator --run <run> -- send lead retry`. Then `wait --run <run> --file artifacts/agents/lead/commands.log --contains OPERATOR_MAX_CHILDREN --timeout 60`. `start wt --dir ...` now exits 0. `start third` exits 2 with `lead already runs 2 children, and OPERATOR_MAX_CHILDREN allows 2. Stop one first.` `artifacts/agents/wt/starts.log` shows `cwd=<run>\repo\.worktrees\wt`.
- **See the tree.** `operator --run <run> -- list`. `scout` and `wt` are indented under `lead`, each ending `child of lead`. `wt` shows the worktree as its directory.
- **Cascade stop.** `operator --run <run> -- stop lead`. Exit 0 in about 2 seconds. Three `Stop signal sent to loop supervisor for '...'` lines, then `stop requested for lead`, `stop requested for scout` and `stop requested for wt`. `list` shows all three offline, still as a tree.
- **Re-home on delete.** `operator --run <run> -- delete lead --yes`. Exit 0, `deleted lead`. `list` shows `scout` and `wt` at the top, with no `child of`. In an `evidence` snapshot, each child's `operators__<id>.json` still names lead's deleted id as `parent`. Nothing rewrites them.

## Gotchas

- **Caps come from the environment of the agent's command.** Setting them after the parent started has no effect on it. Set them in the shell that starts the top-level operator.
- **A cap refusal for a new child has no `operator start:` prefix.** Restarting a stopped child past the cap does print the prefix. Match on `OPERATOR_MAX_CHILDREN` or `OPERATOR_MAX_DEPTH`, never on the prefix.
- **A child starts in the parent's recorded directory**, not wherever the agent's shell is. A `cd` step before `start` does not move the child. A relative `--dir` is resolved against the agent's own working directory.
- **A person has no cap**, and the harness is always a person. Caps and scope are only visible through agent steps.
- **Mail drives the retry.** A person's `send` to a top-level operator is the only way to make an already running fake act again. Give it an `on` handler.
- **An agent's delete needs `--yes`.** Its stdin looks like a terminal on Windows, so `{"op":["delete","scout"]}` prints `Delete? [y/N]`, reads nothing and exits 1. It never sees the `pass --yes` line a person's script gets. #50 holds the evidence.
