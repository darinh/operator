"""The operator_bench package imports without pulling in the kernel."""
from __future__ import annotations

import operator_bench


def test_operator_bench_is_importable():
    assert operator_bench.__doc__
    assert callable(operator_bench.measure)
