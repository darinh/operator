# Mail

`operator send NAME "text"` posts a message to an operator's mailbox. The operator's own supervisor types it into the session as one line that names the sender. Mail travels only between a parent and its child. A person counts as the parent of every top-level operator and reads mail from them with `operator inbox`. `operator list` says when such mail is waiting. Mail to a stopped operator waits, and arrives in its next session.

## Sub-features

- `send-down` a parent's message reaches its child, labelled `your parent`.
- `send-up` a child's message reaches its parent, labelled `your child`.
- `person-to-top` a person's message reaches a top-level operator, labelled `the person who started you`.
- `top-to-person` a top-level operator's `send human` lands in the person's mailbox.
- `inbox` prints the person's waiting mail once, then `No messages.`.
- `list-count` a person's `list` ends with the waiting count. An agent's `list` never shows it.
- `edges-only` every other pair refuses, and nothing is written.
- `length` 4000 characters arrive intact. 4001 refuse.
- `flatten` anything Python does not count as printable becomes a space: control characters, newlines, tabs, and separators such as a non-breaking space. Leading and trailing spaces are dropped. Everything else arrives as typed.
- `queued` mail to a stopped operator stays pending and is typed into its next session. It arrived once in every run measured. The kernel promises at least once, so a supervisor that dies between typing and filing a message types it again.

## How to get to it (user POV)

- A person runs `operator send NAME "text"`, `operator inbox` and `operator list` at a shell.
- An agent runs `operator send NAME "text"` or `operator send human "text"`. Its preamble names the exact command for its parent and its children.
- Mail is only on the command line. The menu has no send and no inbox, and the README says why.

## Driving it with control_operator

Preconditions:

- `up` has run and `doctor --run <run>` printed `doctor: healthy`.
- Both scripts are written before `start lead`.

- **Script both sides.** `agent --run <run> lead` with `{"op":["start","scout","role=scout"]}`, `{"on":"hello","do":[{"op":["send","scout","ping"]}]}` and `{"on":"report","do":[{"op":["send","human","lead finished"]},{"op":["list"]},{"op":["inbox"]}]}`. `agent --run <run> scout` with `{"op":["send","lead","hello"]}`, `{"op":["send","human","skip my parent"]}`, `{"op":["send","nobody","x"]}` and `{"on":"ping","do":[{"op":["send","lead","pong"]}]}`.
- **Exchange.** `operator --run <run> -- start lead "role=lead"`, then `wait --run <run> --file artifacts/agents/lead/stdin.log --contains "] pong" --timeout 90`. Found after about 23 seconds.
  - lead's `stdin.log`: `[operator message from scout (op-…), your child] hello`, then `… your child] pong`.
  - scout's `stdin.log`: `[operator message from lead (op-…), your parent] ping`.
  - scout's `commands.log`: `send human` exits 2 with `operator send: human is not your parent or your child, and mail goes only between those two.` `send nobody` exits 1 with `No operator 'nobody'.`
- **Mail for the person.** `operator --run <run> -- send lead report`. Then `wait --run <run> --file "home/mail/human/pending/*.json" --timeout 30`.
  - The person's `list` ends with `1 message(s) waiting. Read them with: operator inbox`. lead's own `list` in its `commands.log` has no such line, and its `inbox` printed `No messages.`
  - The person's `inbox` prints `<sent>  [operator message from lead (op-…), an operator you started] lead finished`. A second `inbox` prints `No messages.`, and `list` drops the count.
- **Odd text.** Send `@file /help !dir "quoted" %USERNAME% & café`, then a tab, `here`, a newline and `second line`. lead's `stdin.log` gets `[operator message from the person who started you] @file /help !dir "quoted" %USERNAME% & café tab here second line`. Nothing is expanded, the tab and the newline become spaces, and the header keeps `/` and `@` out of the line's first column.
- **Refusals from a person.**
  - 4001 characters: exit 2, `operator send: a message may hold 4000 characters, and this one holds 4001.`
  - 4000 characters: `sent to lead`, and the line arrives whole.
  - `send nobody hi`: exit 1, `No operator 'nobody'.`
  - `send scout "not yours"` to a child: exit 2, `operator send: scout is not your parent or your child, and mail goes only between those two.`
  - `send lead` with no text: exit 2, `Usage: operator send NAME "message"`.
- **Queued.** `operator stop lead`, then `send lead "while you were out"`. `home/mail/<lead id>/pending/` holds one file. `operator start lead`, then wait for `while you were out` in lead's `stdin.log`. Found after 12 seconds, and still exactly one copy 12 seconds later.

## Gotchas

- **Never wait on label words.** Every message from a person starts `[operator message from the person who started you]`, so `--contains "from the person"` matches the first one ever sent, after 0.0 seconds. Wait on text only that message holds, or on `] TEXT`.
- **Delivery is one supervisor poll.** A hop takes up to about 10 seconds. A reply sent while the supervisor is already polling can land in under a second. Give each hop a 30 second timeout.
- **Delivered mail stays on disk.** Files move from `pending/` to `delivered/` under `home/mail/<id>/`. Count `pending/` to see what is waiting. `delete` removes the whole mailbox.
- **An agent's inbox is nearly always empty**, because its supervisor types mail into the session first. The person's inbox is the one to read.
- **A person may write only to top-level operators.** To reach a child, script its parent with an `on` handler that forwards the text.
