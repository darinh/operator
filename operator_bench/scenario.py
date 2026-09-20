"""Program the child may see, and Oracle labels that must not cross to it."""
from __future__ import annotations

from dataclasses import dataclass

WORK = "work"
BUSYWORK = "busywork"
SILENCE = "silence"
LAUNCH_FAIL = "launch_fail"

HANDOFF = "handoff"
EXIT = "exit"
UNACCOUNTED = "unaccounted"
STOP = "stop"

DETECTION = "detection"
MISS = "miss"
TRUE_NEGATIVE = "true-negative"
FALSE_ALARM = "false-alarm"

_EFFECTS = frozenset({WORK, BUSYWORK, SILENCE, LAUNCH_FAIL})
_ENDINGS = frozenset({HANDOFF, EXIT, UNACCOUNTED, STOP, LAUNCH_FAIL})
_CHILD_KEYS = frozenset({
    "instance", "user_args", "max_virtual_seconds", "max_sleeps", "sessions",
})


@dataclass(frozen=True)
class Session:
    duration_s: float
    effect: str
    ending: str

    def __post_init__(self) -> None:
        if self.effect not in _EFFECTS:
            raise ValueError(f"unmodelled effect {self.effect!r}")
        if self.ending not in _ENDINGS:
            raise ValueError(f"unmodelled ending {self.ending!r}")


@dataclass(frozen=True)
class Program:
    name: str
    instance: str
    sessions: tuple[Session, ...]
    max_virtual_seconds: float = 20_000.0
    max_sleeps: int = 200_000
    user_args: tuple[str, ...] = ("--agent", "bench:seat")

    def request(self) -> dict:
        """Payload the child receives. Contains no Oracle fields."""
        payload = {
            "instance": self.instance,
            "user_args": list(self.user_args),
            "max_virtual_seconds": self.max_virtual_seconds,
            "max_sleeps": self.max_sleeps,
            "sessions": [
                {
                    "duration_s": session.duration_s,
                    "effect": session.effect,
                    "ending": session.ending,
                }
                for session in self.sessions
            ],
        }
        if set(payload) != _CHILD_KEYS:
            raise RuntimeError("child request grew a key Oracle could hide in")
        return payload


@dataclass(frozen=True)
class Oracle:
    stalled_from: int | None
    stop_required_by: int | None
    any_stop_is_false_alarm: bool
    expected: str
    label_source: str
    horizon_sessions: int


@dataclass(frozen=True)
class Scenario:
    program: Program
    oracle: Oracle
