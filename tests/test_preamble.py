"""The launch preamble never tells the agent to economise."""
from __future__ import annotations

from pathlib import Path

_ECONOMY = (
    "budget", "quota", "frugal", "econom", "cheaper", "spend less",
    "cost ceiling", "token cap", "save tokens", "be careful", "thrift",
)


def test_preamble_source_has_no_economy_instruction():
    text = (Path(__file__).resolve().parent.parent / "operator_kernel"
            / "preamble.py").read_text(encoding="utf-8").lower()
    for word in _ECONOMY:
        assert word not in text, word
