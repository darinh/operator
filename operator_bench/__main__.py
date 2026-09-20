"""CLI: python -m operator_bench measure"""
from __future__ import annotations

import sys

from operator_bench.score import format_scorecard
from operator_bench.suite import measure


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if not argv or argv[0] != "measure":
        print("usage: python -m operator_bench measure")
        return 2
    print(format_scorecard(measure()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
