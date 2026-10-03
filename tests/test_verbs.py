"""The verb table is what `operator --help` lists.

The keyboard menu does not read it. A verb with no handler is the drift
this file refuses.
"""
from __future__ import annotations

from operator_cli import entry as cli
from operator_cli import verbs


def test_every_verb_in_the_table_has_somewhere_to_go():
    for verb in verbs.VERBS:
        assert verb.tokens[0] in cli.HANDLERS, verb.tokens


def test_every_handler_is_in_the_table():
    names = {verb.tokens[0] for verb in verbs.VERBS}
    assert names == set(cli.HANDLERS)


def test_every_verb_has_help_text():
    for verb in verbs.VERBS:
        assert verb.help.strip(), verb.tokens


def test_entry_still_re_exports_the_table():
    assert cli.VERBS is verbs.VERBS


def test_help_says_start_takes_a_task(capsys):
    assert cli.main(["--help"]) == 0
    assert ("  start                 start a supervised operator (start [NAME] [TASK])"
            in capsys.readouterr().out.splitlines())
