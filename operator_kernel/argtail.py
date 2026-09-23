"""`--` ends operator flags. Generated flags go in front of it.

Every parser walks `at_dashdash(argv)[0]`. Every extra flag is inserted with
`before_terminator`. A consumer that concatenates onto argv instead puts
operator options into the literal tail.
"""
from __future__ import annotations


def at_dashdash(argv: list[str]) -> tuple[list[str], list[str]]:
    argv = list(argv)
    try:
        i = argv.index("--")
    except ValueError:
        return argv, []
    return argv[:i], argv[i:]


def before_terminator(argv: list[str], extra: list[str]) -> list[str]:
    options, literal = at_dashdash(argv)
    return [*options, *extra, *literal]
