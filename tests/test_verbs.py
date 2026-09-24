"""The verb table is the only place that says what `operator` can do.

Three surfaces read it: the help text, the numbered menu, and the argv the
menu builds. A verb listed but unroutable, or routable but unreachable from
the menu, is the drift this file exists to refuse.
"""
from __future__ import annotations

from operator_cli import entry as cli
from operator_cli import verbs


def test_every_verb_in_the_table_has_somewhere_to_go():
    for verb in verbs.VERBS:
        assert verb.tokens[0] in cli.HANDLERS, verb.tokens


def test_every_prompt_a_verb_asks_for_has_a_label():
    """A prompt key with no label raises KeyError at the prompt, which is
    mid-menu, in front of a human, after they picked the item."""
    for verb in verbs.VERBS:
        for key in verb.prompts:
            assert key in verbs.PROMPT_LABEL, (verb.tokens, key)


def test_the_menu_offers_every_verb_and_every_extra():
    offered = {item.argv for item in verbs.menu_items()}
    for verb in verbs.VERBS:
        assert verb.tokens in offered, verb.tokens
        for _, argv in verb.extra_menu:
            assert argv in offered, argv


def test_a_flagged_prompt_becomes_a_flag_and_a_positional_stays_bare():
    """`--status` is the case that forced this distinction. Appending it
    positionally would have handed the handoff verb a seat name."""
    item = verbs.Item("x", ("handoff",), ("instance", "status"))
    assert verbs.build_argv(item, {"instance": "alpha", "status": "done"}) == [
        "handoff", "--instance", "alpha", "--status", "done"]
    item = verbs.Item("x", ("join",), ("name",))
    assert verbs.build_argv(item, {"name": "alpha"}) == ["join", "alpha"]


def test_instance_goes_in_front_of_the_verbs_own_tokens():
    """`operator-seat` declares --instance on the top-level parser, so it has
    to precede the subcommand."""
    item = verbs.Item("x", ("remember",), ("instance", "kind", "text"))
    assert verbs.build_argv(
        item, {"instance": "alpha", "kind": "gotcha", "text": "note"}) == [
        "remember", "--instance", "alpha", "--kind", "gotcha", "note"]


def test_entry_still_re_exports_what_callers_import_from_it():
    """The table moved out of `entry.py` under its line ceiling. Anything
    that imported it from there must keep working."""
    assert cli.VERBS is verbs.VERBS
    assert cli.menu_items is verbs.menu_items
