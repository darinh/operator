"""Settings the kernel reads from the environment when asked, not at import."""
from __future__ import annotations

import pytest

import config


@pytest.mark.parametrize("read, variable, default", [
    (config.max_children, "OPERATOR_MAX_CHILDREN", 4),
    (config.max_depth, "OPERATOR_MAX_DEPTH", 3),
])
def test_a_cap_follows_its_variable_and_ignores_nonsense(monkeypatch, read, variable,
                                                         default):
    assert read() == default
    monkeypatch.setenv(variable, "9")
    assert read() == 9
    for nonsense in ("", "0", "-2", "many", "2.5"):
        monkeypatch.setenv(variable, nonsense)
        assert read() == default, nonsense
