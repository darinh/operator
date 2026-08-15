"""Extracted from copilot_operator.py. See docs/spike-extraction.md."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
import uuid
import hashlib
import sqlite3
import signal
import contextlib
import ntpath
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
import instance

from config import BASE_CLAUSES, CODE_CURRENT, CODE_MISMATCH, CODE_STALE
from instance import Instance
from claims import claims
from mandate import (Mandate, authority_clause, assert_no_unattributed_authority,
                     vet_clause)

def build_preamble(agent_name: str, instance: Instance, crash_recovery: bool = False,
                   assignment: str = "", code_state: str = CODE_CURRENT,
                   mandate: "Mandate | None" = None,
                   on_withheld=None) -> str:
    """Compose the launch preamble from mechanism plus attributed authority.

    Two kinds of sentence go to a session, and they are kept apart on purpose.

    **Mechanism** is what this supervisor does: it relaunches, it reads a
    handoff file, it is not being watched. The supervisor may assert those
    because they are claims about its own behaviour, checkable against it.

    **Authority** is what the agent is permitted to do. That comes from
    `mandate.authority_clause` and nowhere else. Backlog 0013 -- one
    unattributed sentence granting "blanket human approval for ALL decisions",
    which reached every session and was later quoted back to the owner as his
    own instruction -- was written in the first person of this function, and
    survived the extraction into this kernel intact. `mandate.py` is why it
    cannot be written here again, and
    `tests/test_preamble_authority.py` is what fails if it is.

    Note what is *not* said any more: nothing here tells the agent to make its
    best judgement and proceed, because whether it may do that is a question
    about its authority. Where no mandate is on file the agent is told exactly
    that, which is a weaker and truer thing than the sentence it replaces.
    """
    text = (
        "You are running unattended under the operator supervisor, which launched "
        "this session and will relaunch it if it dies. Key facts: (1) Nobody is "
        "reading this session while it runs, so a prompt addressed to the user is "
        "not seen by anyone and the seat idles until something kills it. "
        "(2) Session restart: when context gets heavy or a task is "
        "complete with next steps, use the handoff command: handoff --instance "
        f"{instance.display_name} --status \"what you completed\" --next \"what to do next\" "
        "--context \"key decisions and gotchas\" — this atomically writes the handoff file "
        "and triggers the restart. It works the same on every platform. (3) On startup: "
        "always check for a session handoff file to resume work. (4) You are the "
        f"@{agent_name} agent, and the harness will not stop to confirm individual "
        "tool calls, file edits or commands — which is a fact about the harness, not "
        "a grant of permission to use it for a given purpose. "
        f"(5) Operator instance: {instance.display_name}. "
        "Now: check for your session handoff and get to work."
    )
    clauses: list[str] = [authority_clause(mandate)]
    if crash_recovery:
        clauses.append(
            "This session is being resumed because a handoff file could not be "
            "found for this project. Either a crash occurred or the previous session "
            "ended without the handoff being written. If you intended to end the "
            "session, please make sure you write a handoff first next time."
        )
    notice = _code_state_notice(code_state, instance, crash_recovery)
    if notice:
        clauses.append(notice)
    # The assignment is resolved by `operator session start` before the agent's
    # first token (FR-2), and reaches it here already rendered. Nothing is said
    # when there is nothing to say: an unassigned session passes "", and an
    # always-present line reading "you have no assignment" would be paid for on
    # every token of every session that has none.
    #
    # A string rather than an object this function describes. It used to call
    # `operator_session.describe(assignment)` -- a module the kernel boundary
    # forbids and which `preamble.py` never imported, so the call was a latent
    # `NameError`. Describing a work item is not supervision; hand the kernel
    # the sentence instead of the thing that can produce it.
    #
    # Vetted rather than trusted, because the work database is written by
    # agents. This is the only clause today with a non-kernel author, and it is
    # the reason the scan below cannot simply raise.
    if assignment:
        clause, withheld = vet_clause(assignment, "this session's work item")
        clauses.append(clause)
        if withheld and on_withheld is not None:
            # Reported rather than returned, so that adding a vetted clause
            # never changes this function's signature and so that a caller
            # cannot forget to vet -- the vetting is here, and the callback
            # only decides where the detail is written down.
            on_withheld("this session's work item", withheld)
    # Numbered from the clauses actually collected, rather than from a counter
    # incremented alongside them. Both spellings produce the same text today;
    # they differ in what they make *possible*. A counter is two statements --
    # bump it, append the text -- and nothing ties them together, so it can be
    # bumped without appending (leaving a gap in the numbering) or appended to
    # without bumping (using a number twice). The literal "(7)" that this
    # replaced was the second of those, and it survived because at the time it
    # was written only one optional clause could precede it.
    #
    # Numbering a list at render time makes both unrepresentable: a clause that
    # is not appended cannot consume a number, because the number *is* its
    # index. This is deliberately not a test -- a guard that has to fire is
    # weaker than a shape that cannot fail, and this one previously cost a
    # surviving mutant that could only be argued equivalent by reasoning about
    # which clause happened to be last.
    for offset, body in enumerate(clauses):
        text += f" ({BASE_CLAUSES + 1 + offset}) {body}"
    # Every clause the kernel did not author has already been through
    # `vet_clause`, which withholds rather than raises. What is left to check
    # is the kernel's own literal text plus the no-mandate clause -- and the
    # only thing that can make *those* grant is a kernel bug, so raising is
    # right here and a raise can no longer be provoked from outside. It used to
    # be: the assignment clause is rendered from the work database, agents
    # write that database, and a raise here is caught by nothing in
    # `run_loop_mode`, so a granting phrase in a backlog item permanently
    # killed that seat's supervisor. A reviewer traced it.
    # Exempt the mandate's own words, and only when a mandate exists. A human
    # may grant anything; the rule is that a human said it. When there is no
    # mandate the clause is the kernel's own refusal text, which has no human
    # behind it either -- exempting it would carve a hole exactly the shape of
    # the sentence this module exists to keep out. The first draft did exempt
    # it, and only the control that tries to smuggle a grant through that
    # clause revealed it.
    assert_no_unattributed_authority(
        text, attributed=clauses[0] if mandate is not None else None)
    return text


def _code_state_notice(code_state: str, instance: Instance,
                       crash_recovery: bool) -> str:
    """The staleness caveat for the preamble, or ``""`` when there is none.

    Why this belongs in the preamble at all, when `operator list` already
    reports the same fact: they have different readers. `operator list` is
    read by a human at a terminal who went looking. The preamble is read by
    an agent that did not, and the preamble is where the misinformation
    lands -- 355 launches across the fleet were told a handoff could not be
    found, by supervisors running code from before the verdict was decided
    per launch, and not one of them had any reason to go and check whether
    its wrapper was current. Making staleness legible at the command line
    did not make it legible to the party being lied to.

    Scoped deliberately to *this preamble's own claims* rather than issued as
    a general warning about the repository. The agent cannot act on "some
    code is old"; it can act on "the sentence above about your predecessor
    may have been written by code that no longer exists".

    Silent when the code is current, which is the overwhelmingly common case
    -- a caveat attached to every session is one that stops being read, and
    this instrument exists because the previous one said nothing.
    """
    if code_state == CODE_CURRENT:
        return ""
    # Named so the agent can quote it back, and so the two verdicts are not
    # reported in the same words: one is an observed difference, the other is
    # an absence of evidence, and collapsing them would overstate the second.
    claims = ("the claim above that a handoff could not be found"
              if crash_recovery else "anything above that it decided per launch")
    if code_state == CODE_STALE:
        return (
            "CAUTION — this operator wrapper is running OUT-OF-DATE code. The operator "
            "source on disk has changed since the supervisor that launched you imported "
            f"it, and a supervisor keeps its code for the whole run, so {claims} was "
            "produced by a version that is no longer in the tree. Treat it as "
            "unverified rather than false, and verify anything you would otherwise "
            "have taken on this wrapper's word before acting on it — in particular, "
            "check for a handoff file yourself rather than trusting a claim that none "
            "exists. This is an observation about YOUR supervisor only — it is made by "
            "comparing this process's own loaded code against disk, and says nothing "
            "about any other instance, which may have started at a different time. "
            "DO NOT run any restart command yourself, not even as a step in a larger "
            "task: restarting a supervisor is a decision about the process you are "
            "running under. Report this to the human and let them decide; "
            "`operator list` names the changed files and every instance affected."
        )
    if code_state == CODE_MISMATCH:
        # Deliberately not the fall-through below, which says the supervisor
        # "either recorded nothing [...] or that record could not be compared".
        # Both are false here: a record was read, and it names a different
        # process. Reaching this branch is currently impossible -- the
        # preamble's verdict comes from `own_code_state`, which compares this
        # process's in-memory fingerprint and never consults a pid -- but a
        # wrong default that is merely unreachable today is the enum-extension
        # defect waiting to happen, and adversarial review asked for it by
        # name.
        return (
            "CAUTION — this operator wrapper CANNOT SHOW that it is running current "
            "code. The startup record for the supervisor that launched you belongs to "
            "a different process: one supervisor was replaced by another that could "
            f"not overwrite it. So {claims} cannot be attributed to the code you are "
            "actually running, and nothing in that record describes it — not which "
            "source it loaded, nor when it started. Verify anything load-bearing — in "
            "particular, check for a handoff file yourself rather than trusting a "
            f"claim that none exists. `operator restart-loop {instance.display_name}` "
            "writes a fresh record, but that restarts the supervisor you are running "
            "under, so raise it with the human rather than doing it as a side effect "
            "of some other task."
        )
    return (
        "CAUTION — this operator wrapper CANNOT SHOW that it is running current code. "
        "The supervisor that launched you either recorded nothing about the operator "
        "source it imported, or that record could not be compared against the tree "
        f"now. This is an absence of evidence, not evidence of staleness: {claims} may "
        "be perfectly correct. But it cannot be confirmed, so verify anything load-"
        "bearing — in particular, check for a handoff file yourself rather than "
        "trusting a claim that none exists. `operator list` reports the same state for "
        "every instance on this machine."
    )
