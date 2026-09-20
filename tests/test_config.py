"""Ceilings are configurable and unlimited by default."""
from __future__ import annotations

import os


def test_spend_ceiling_is_none_when_unset():
    import config
    assert config.spend_ceiling({}) is None
    assert config.spend_ceiling({"OPERATOR_SPEND_CEILING": ""}) is None
    assert config.spend_ceiling({"OPERATOR_SPEND_CEILING": "  "}) is None


def test_spend_ceiling_is_none_when_unreadable():
    import config
    assert config.spend_ceiling({"OPERATOR_SPEND_CEILING": "nope"}) is None
    assert config.spend_ceiling({"OPERATOR_SPEND_CEILING": "NaN"}) is None


def test_spend_ceiling_reads_a_number():
    import config
    assert config.spend_ceiling({"OPERATOR_SPEND_CEILING": "8"}) == 8.0
    assert config.spend_ceiling({"OPERATOR_SPEND_CEILING": "2.5"}) == 2.5


def test_imported_ceiling_defaults_to_none():
    import config
    if not os.environ.get("OPERATOR_SPEND_CEILING", "").strip():
        assert config.SPEND_CEILING is None
