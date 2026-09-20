"""Seat spend is a file the kernel reads. The kernel does not compute cost."""
from __future__ import annotations

import json
from pathlib import Path


def spend_path(home, seat_id: str) -> Path:
    name = Path(str(seat_id)).name
    if not name or name != str(seat_id) or name in {".", ".."}:
        name = "_"
    return Path(home) / "spend" / f"{name}.json"


def seat_spend(home, seat_id) -> float | None:
    figure = seat_figure(home, seat_id)
    return None if figure is None else figure[0]


def seat_figure(home, seat_id):
    """Read the figure filed under `seat_id`, which is `instance.id`.

    One key, not a search. An earlier version tried the display name too, but
    `safe_instance_id` is not idempotent, so two candidate filenames meant the
    gate and the cost recorder could disagree about which one was canonical.
    """
    return _load(spend_path(home, str(seat_id)))


def spend_blocks(home, seat_id, ceiling) -> tuple | None:
    if ceiling is None:
        return None
    amount = seat_spend(home, seat_id)
    if amount is None:
        return None
    if amount >= ceiling:
        return ("spend-ceiling", "seat spend meets the configured ceiling")
    return None


def _load(path: Path):
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError):
        return None
    if not isinstance(raw, dict):
        return None
    amount = raw.get("amount")
    if isinstance(amount, bool) or not isinstance(amount, (int, float)):
        return None
    if amount != amount:
        return None
    unit = raw.get("unit")
    source = raw.get("source")
    return (
        float(amount),
        None if unit is None else str(unit),
        None if source is None else str(source),
    )
