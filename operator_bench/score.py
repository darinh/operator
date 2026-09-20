"""Estimates, measurements, Wilson intervals. No bare-float score."""
from __future__ import annotations

import math
from dataclasses import dataclass

from operator_bench.observe import Observation
from operator_bench.scenario import (
    DETECTION, FALSE_ALARM, MISS, TRUE_NEGATIVE, Oracle,
)

EXIT_NO_PROGRESS = 3
EXIT_UNACCOUNTED = 4
WILSON = "Wilson"
_Z95 = 1.96


@dataclass(frozen=True)
class Exact:
    value: float


@dataclass(frozen=True)
class Estimated:
    value: float
    lo: float
    hi: float


@dataclass(frozen=True)
class NoEstimate:
    reason: str


Estimate = Exact | Estimated | NoEstimate


@dataclass(frozen=True)
class Measurement:
    estimate: Estimate
    n: int
    eligible: int
    censored: int
    coverage: float
    label_source: str
    horizon: str

    def __post_init__(self) -> None:
        if not isinstance(self.estimate, (Exact, Estimated, NoEstimate)):
            raise TypeError("Measurement requires an Estimate, not a bare float")


@dataclass(frozen=True)
class Latency:
    polls: int | None
    censored: bool
    bound_polls: int


@dataclass(frozen=True)
class ScenarioScore:
    name: str
    outcome: str
    exit_code: int
    latency: Latency
    error: str | None


@dataclass(frozen=True)
class Scorecard:
    scenarios: tuple[ScenarioScore, ...]
    miss_rate: Measurement
    false_alarm_rate: Measurement
    detection_latency: Measurement


def wilson(k: int, n: int, z: float = _Z95) -> tuple[float, float, float]:
    if n <= 0:
        raise ValueError("Wilson interval needs n > 0")
    p = k / n
    z2 = z * z
    denom = 1.0 + z2 / n
    center = (p + z2 / (2.0 * n)) / denom
    margin = z * math.sqrt((p * (1.0 - p) / n) + z2 / (4.0 * n * n)) / denom
    return p, max(0.0, center - margin), min(1.0, center + margin)


def rate(k: int, n: int, *, eligible: int, censored: int,
         label_source: str, horizon: str) -> Measurement:
    coverage = (eligible / n) if n else 0.0
    if eligible <= 0:
        est: Estimate = NoEstimate("no eligible trials")
    else:
        p, lo, hi = wilson(k, eligible)
        est = Estimated(p, lo, hi)
    return Measurement(
        estimate=est, n=n, eligible=eligible, censored=censored,
        coverage=coverage, label_source=label_source, horizon=horizon,
    )


def kernel_stopped(obs: Observation) -> bool:
    if obs.exit_code in (EXIT_NO_PROGRESS, EXIT_UNACCOUNTED):
        return True
    if obs.exit_code == 0:
        return False
    if obs.error:
        return True
    return "Giving up" in obs.log_text or "Progress breaker tripped" in obs.log_text


def classify(obs: Observation, oracle: Oracle) -> str:
    stopped = kernel_stopped(obs)
    if oracle.any_stop_is_false_alarm:
        return FALSE_ALARM if stopped else TRUE_NEGATIVE
    if oracle.stop_required_by is not None:
        return DETECTION if stopped else MISS
    return FALSE_ALARM if stopped else TRUE_NEGATIVE


def latency_for(obs: Observation, oracle: Oracle, outcome: str) -> Latency:
    bound = obs.polls
    if outcome != DETECTION:
        return Latency(polls=None, censored=True, bound_polls=bound)
    start = 0
    if oracle.stalled_from is not None and obs.launch_polls:
        idx = oracle.stalled_from - 1
        if 0 <= idx < len(obs.launch_polls):
            start = obs.launch_polls[idx]
    return Latency(polls=max(0, obs.polls - start), censored=False, bound_polls=bound)


def score_run(name: str, obs: Observation, oracle: Oracle) -> ScenarioScore:
    outcome = classify(obs, oracle)
    return ScenarioScore(
        name=name, outcome=outcome, exit_code=obs.exit_code,
        latency=latency_for(obs, oracle, outcome), error=obs.error,
    )


def scorecard(rows: tuple[ScenarioScore, ...], *,
              label_source: str, horizon: str) -> Scorecard:
    n = len(rows)
    required = tuple(r for r in rows if r.outcome in (DETECTION, MISS))
    misses = sum(1 for r in required if r.outcome == MISS)
    controls = tuple(r for r in rows if r.outcome in (TRUE_NEGATIVE, FALSE_ALARM))
    alarms = sum(1 for r in controls if r.outcome == FALSE_ALARM)
    detections = tuple(r for r in rows if r.outcome == DETECTION)
    uncensored = tuple(r for r in detections if not r.latency.censored
                       and r.latency.polls is not None)
    if not uncensored:
        lat_est: Estimate = NoEstimate("no uncensored detections")
    elif len(uncensored) == 1:
        lat_est = Exact(float(uncensored[0].latency.polls))
    else:
        mean = sum(r.latency.polls or 0 for r in uncensored) / len(uncensored)
        lat_est = Exact(mean)
    lat = Measurement(
        estimate=lat_est, n=len(detections), eligible=len(uncensored),
        censored=len(detections) - len(uncensored),
        coverage=(len(uncensored) / len(detections)) if detections else 0.0,
        label_source=label_source, horizon=horizon,
    )
    return Scorecard(
        scenarios=rows,
        miss_rate=rate(misses, n, eligible=len(required),
                       censored=n - len(required),
                       label_source=label_source, horizon=horizon),
        false_alarm_rate=rate(alarms, n, eligible=len(controls),
                              censored=n - len(controls),
                              label_source=label_source, horizon=horizon),
        detection_latency=lat,
    )


def format_measurement(name: str, m: Measurement) -> str:
    est = m.estimate
    if isinstance(est, NoEstimate):
        body = f"NoEstimate ({est.reason})"
    elif isinstance(est, Exact):
        body = f"{_num(est.value)} exact"
    elif isinstance(est, Estimated):
        body = (
            f"{_num(est.value)} {WILSON} 95% [{_num(est.lo)}, {_num(est.hi)}]"
        )
    else:
        raise TypeError("unknown estimate")
    return (
        f"{name}: {body} n={m.n} eligible={m.eligible} "
        f"censored={m.censored} coverage={_num(m.coverage)} "
        f"labels={m.label_source} horizon={m.horizon}"
    )


def format_scorecard(card: Scorecard) -> str:
    lines = ["operator_bench measure"]
    for row in card.scenarios:
        extra = ""
        if row.outcome == DETECTION and row.latency.polls is not None:
            extra = f" latency {row.latency.polls} polls"
        elif row.latency.censored:
            extra = f" censored at {row.latency.bound_polls} polls"
        lines.append(f"  {row.name}: {row.outcome} exit={row.exit_code}{extra}")
    lines.append(format_measurement("miss_rate", card.miss_rate))
    lines.append(format_measurement("false_alarm_rate", card.false_alarm_rate))
    lines.append(format_measurement("detection_latency", card.detection_latency))
    return "\n".join(lines)


def _num(value: float) -> str:
    if math.isfinite(value) and value == int(value) and abs(value) < 10**12:
        return str(int(value))
    text = f"{value:.4f}"
    return text.rstrip("0").rstrip(".") if "." in text else text
