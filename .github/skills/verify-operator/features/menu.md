# Menu

`operator` with no arguments opens a full-screen menu. It can start an operator here and attach to it. It lists operators as a tree, and acts on one row: attach or stop a running operator, or start, start and attach, rename or delete an offline one. Every action calls the same code as the matching command. The menu ends when it hands the terminal to an attach, or when the person quits.

## Sub-features

- `main` Start an operator, List operators, the recovery row, Quit.
- `start-prompt` names the directory and prefills a name: the operator working here, or else the directory's name, or nothing when several work here.
- `start-new` Enter starts a new operator and attaches the terminal to it.
- `start-running` Enter on the name of an operator already running here attaches to it.
- `start-refused` a bad or taken name shows the reason above the prompt and keeps the prompt open.
- `list` Running and Offline sections, children indented with `child of NAME`.
- `running-actions` Attach and Stop.
- `offline-actions` Start, Start and attach, Rename and Delete.
- `stop` shows what stop printed above the refreshed list.
- `rename` asks for the new name, prefilled with the old one.
- `delete` asks `Delete? [y/N]`. Only `y` deletes. Anything else goes back to the row's actions.
- `escape` goes back one screen, and quits from the main screen.

## How to get to it (user POV)

Run `operator` in a terminal. Up and Down move, Enter picks, Esc goes back. When an attach ends with `C-b d`, the menu has already exited, and the shell comes back. The menu cannot send mail, read the inbox, or start in another worktree. Those are on the command line only. See [mail.md](./mail.md) and [child-operators.md](./child-operators.md).

## Driving it with control_operator

Preconditions:

- `up` has run and `doctor --run <run>` printed `doctor: healthy`.
- For the list and row recipes, a parent `lead` scripted with `{"op":["start","scout","role=scout"]}` was started with `operator --run <run> -- start lead "role=lead"`, and scout's `starts.log` shows `session=1`.
- `menu --run <run>` opened session `vo-<run-id>`. Every step waits on screen text with `wait --run <run> --screen menu --contains T`, then saves the screen with `screen --run <run> --label L`.

- **Main screen.** Wait for `Start an operator`. The rows are `> Start an operator`, `List operators`, `No operators need recovery.` and `Quit`.
- **List.** `keys --run <run> Down Enter`, then wait for `Operators`. The screen shows `Running:`, then `> 1. lead  (<run>\repo)  pid N` and `2.   scout  (<run>\repo)  pid N  child of lead`, then `Offline:` and `(none)`. The cursor starts on the first operator.
- **Running row.** `keys Down Enter` picks scout. The screen is `scout`, then `> Attach` and `Stop`.
- **Stop.** `keys Down Enter`, then wait for `stop requested for scout`. It takes about 2 seconds. Above the list, the screen shows `stop requested for scout` and `[operator <time>]   Stop signal sent to loop supervisor for 'scout' (pid N)`. scout is now under `Offline:` as `1.   scout  (<run>\repo)  child of lead`.
- **Offline row.** `keys Down Enter`. The screen is `scout`, then `> Start`, `Start and attach`, `Rename` and `Delete`.
- **Rename.** `keys Down Down Enter`. The screen is `Operator name:` with `scout` filled in. Send `keys BSpace` five times, then `keys --text scouty Enter`, and wait for `renamed scout to scouty`.
- **Delete.** Pick scouty, then `keys Down Down Down Enter`. The screen is `Deletes operator scouty and all of its settings.`, `Repo: <run>\repo` and `Delete? [y/N]`. `keys n` goes back to scouty's actions. Choose Delete again and `keys y`, then wait for `deleted scouty`.
- **Start prompt.** `keys Escape` back to the main screen, then `keys Enter`. The screen is `Start an operator in <run>\repo`, `Enter starts it and attaches this terminal. Esc goes back.` and `Name:`, prefilled with `lead`. Leave it open for the next recipe.
- **Start running.** `keys Enter` on the prefilled `lead`. The pane shows lead's session, so wait for `steps done, listening`. lead's `starts.log` still has one line, `session=1`: nothing new started. `keys C-b d`, then wait for `[operator exited`. The screen ends `lead is already running` then `[operator exited 0]`.
- **Refused name.** `menu --run <run>` again. It prints `menu: closed the old vo-<run-id> first` and opens on the main screen. `keys Enter` opens the prompt prefilled with `lead`. Send `keys BSpace` four times, then `keys --text human Enter`. The prompt stays open, with `'human' is reserved for the person who starts operators` above it. The field still holds `human`.
- **Start new.** Send `keys BSpace` five times to clear `human`, then `keys --text solo Enter`. Wait for `artifacts/agents/solo/starts.log` with `session=1`, then for `operator solo (` on the menu screen. The pane now shows solo's session. `keys C-b d` detaches. Wait for `[operator exited`, and the screen ends `started solo (pid N)` then `[operator exited 0]`.
- **Start and attach.** `operator -- stop solo`, then run `menu` again. Choose List operators, pick solo under Offline, then `Start and attach`. Wait for `session 2`. `C-b d` gives `started solo (pid N)` and `[operator exited 0]`.
- **Attach.** On a running row, Enter on `Attach`. Wait for `steps done, listening`, then `C-b d` gives `[operator exited 0]`.
- **Quit.** On the main screen, `keys Escape`. The pane prints `[operator exited 0]`, and the transcript notes `menu: operator exited 0`.

## Gotchas

- **Prefill decides what Enter does.** With one operator working in the directory, the start prompt is prefilled with its name, and Enter attaches to it. It does not refuse. Clear the field before typing a new name. lead has four letters, so that is four `BSpace`.
- **Every attach ends the menu.** After `C-b d` the pane shows `[operator exited 0]` and stays, so read the last screen. Run `menu` again for a fresh one. It closes the old pane first.
- **Keys after an attach go to the operator.** While attached, everything `keys` sends lands in the operator's session, not the menu. Wait for `[operator exited` before driving the menu again.
- **Attaching through the menu leaves terminal replies in the operator's input.** The nested attach answers colour queries, and `]10;rgb:…]11;rgb:…]4;…` lands unsubmitted in the session's input line. The next typed line is appended to it: measured twice, the next mail arrived as `]10;rgb:…[operator message from the person who started you] after attach`. The kernel's `/exit` is appended the same way, so stop never sees it. Stop then waits out its 20 second grace and kills the session: stopping a lead the menu had attached took 27 seconds, against about 2. Never assert mail or stop timing on an operator the menu attached. Use one the menu never attached. The harness forces this nesting by hiding the pane. Whether an attach from a real terminal leaves the same replies is not measured. #49 holds the evidence.
- **Recovery is not reachable here.** `Recover operator sessions (N)` appears only after a supervisor dies without a clean stop. The harness has no recipe for that yet.
