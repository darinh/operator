"""operator_bench's public surface is importable and exposes measure."""
from __future__ import annotations

import operator_bench


def test_operator_bench_is_importable():
    assert callable(operator_bench.measure)
