"""Estimates, measurements, Wilson intervals. No bare-float score."""
from __future__ import annotations

import math
from dataclasses import dataclass

from operator_bench.observe import Observation
from operator_bench.scenario import (
    DETECTION, FALSE_ALARM, INVALID, MISS, TRUE_NEGATIVE, Oracle,
)

EXIT_NO_PROGRESS = 3
EXIT_UNACCOUNTED = 4
EXIT_GIVE_UP = 1
STOP_EXITS = frozenset({EXIT_GIVE_UP, EXIT_NO_PROGRESS, EXIT_UNACCOUNTED})
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
        if self.n < 0 or self.eligible < 0 or self.censored < 0:
            raise ValueError("Measurement counts cannot be negative")
        if self.n + self.censored > self.eligible:
            raise ValueError("n + censored must be <= eligible")


@dataclass(frozen=True)
class Latency:
    polls: int | None
    censored: bool
    bound_polls: int
    reason: str | None = None


@dataclass(frozen=True)
class ScenarioScore:
    name: str
    outcome: str
    exit_code: int
    latency: Latency
    error: str | None
    required_stop: bool
    completed_sessions: int
    verdict_covered: int
    chain: str
    spend_facts: int = 0
    spend_available: int = 0
    ceiling_held: bool | None = None


@dataclass(frozen=True)
class Scorecard:
    scenarios: tuple[ScenarioScore, ...]
    miss_rate: Measurement
    false_alarm_rate: Measurement
    detection_latency: Measurement
    verdict_in_ledger_coverage: Measurement
    ledger_chain_verified: Measurement
    spend_recorded_coverage: Measurement
    spend_ceiling_fidelity: Measurement


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
    coverage = (n / eligible) if eligible else 0.0
    if n <= 0:
        est: Estimate = NoEstimate(
            "no eligible trials" if eligible <= 0 else "no observed trials")
    else:
        p, lo, hi = wilson(k, n)
        est = Estimated(p, lo, hi)
    return Measurement(
        estimate=est, n=n, eligible=eligible, censored=censored,
        coverage=coverage, label_source=label_source, horizon=horizon,
    )


def kernel_stopped(obs: Observation) -> bool:
    return obs.exit_code in STOP_EXITS


def classify(obs: Observation, oracle: Oracle) -> str:
    if obs.error is not None:
        expected_error = oracle.expected_error
        if expected_error is None or expected_error not in obs.error:
            return INVALID
    expected = oracle.expected_exit
    if oracle.any_stop_is_false_alarm:
        if obs.exit_code == 0:
            return TRUE_NEGATIVE
        if obs.exit_code in STOP_EXITS:
            return FALSE_ALARM
        return INVALID
    if oracle.stop_required_by is not None:
        if expected is not None and obs.exit_code == expected:
            return DETECTION
        if obs.exit_code == 0:
            return MISS
        return INVALID
    if obs.exit_code == 0:
        return TRUE_NEGATIVE
    if obs.exit_code in STOP_EXITS:
        return FALSE_ALARM
    return INVALID


def latency_for(obs: Observation, oracle: Oracle, outcome: str) -> Latency:
    bound = obs.polls
    if outcome != DETECTION:
        return Latency(polls=None, censored=True, bound_polls=bound)
    if oracle.stalled_from is None:
        return Latency(
            polls=None, censored=True, bound_polls=bound,
            reason="oracle declares no onset")
    start = 0
    if obs.launch_polls:
        idx = oracle.stalled_from - 1
        if 0 <= idx < len(obs.launch_polls):
            start = obs.launch_polls[idx]
    return Latency(polls=max(0, obs.polls - start), censored=False, bound_polls=bound)


def ledger_verdict_counts(obs: Observation) -> tuple[int, int]:
    """Completed sessions, and how many of those have a progress_verdict.

    A completed session is a `session_exit`. Coverage is per session number,
    not a raw event-count ratio, so extra verdicts cannot wash out a miss.
    """
    verdicts = {r.get("session") for r in obs.records
                if r.get("event") == "progress_verdict"}
    completed = obs.session_exits
    covered = sum(1 for r in completed if r.get("session") in verdicts)
    return len(completed), covered


def score_run(name: str, obs: Observation, oracle: Oracle) -> ScenarioScore:
    outcome = classify(obs, oracle)
    required = (oracle.stop_required_by is not None
                and not oracle.any_stop_is_false_alarm)
    completed, covered = ledger_verdict_counts(obs)
    costs = {r.get("session") for r in obs.records
             if r.get("event") == "session_cost"}
    cap = oracle.spend_ceiling
    avail = len(obs.session_exits) if cap is not None else 0
    facts = sum(1 for r in obs.session_exits if r.get("session") in costs)
    held = None if cap is None else (0 < len(obs.launch_polls) <= cap)
    return ScenarioScore(
        name=name, outcome=outcome, exit_code=obs.exit_code,
        latency=latency_for(obs, oracle, outcome), error=obs.error,
        required_stop=required, completed_sessions=completed,
        verdict_covered=covered, chain=obs.chain,
        spend_facts=facts, spend_available=avail, ceiling_held=held)


def scorecard(rows: tuple[ScenarioScore, ...], *,
              label_source: str, horizon: str) -> Scorecard:
    core = tuple(r for r in rows if r.ceiling_held is None)
    required = tuple(r for r in core if r.required_stop)
    observed_req = tuple(r for r in required if r.outcome in (DETECTION, MISS))
    misses = sum(1 for r in observed_req if r.outcome == MISS)
    controls = tuple(r for r in core if not r.required_stop)
    observed_ctl = tuple(
        r for r in controls if r.outcome in (TRUE_NEGATIVE, FALSE_ALARM))
    alarms = sum(1 for r in observed_ctl if r.outcome == FALSE_ALARM)
    detections = tuple(r for r in core if r.outcome == DETECTION)
    numbered = tuple(r for r in detections if r.latency.polls is not None)
    if not numbered:
        lat_est: Estimate = NoEstimate("no uncensored detections")
        lat_elig = len(detections)
        lat_n, lat_cens = 0, lat_elig
    elif len(numbered) == 1:
        lat_est = Exact(float(numbered[0].latency.polls or 0))
        lat_n, lat_elig, lat_cens = 1, 1, 0
    else:
        lat_est = NoEstimate("latencies span unrelated breakers")
        lat_n, lat_elig, lat_cens = 0, len(numbered), 0
    lat = Measurement(
        estimate=lat_est, n=lat_n, eligible=lat_elig, censored=lat_cens,
        coverage=(lat_n / lat_elig) if lat_elig else 0.0,
        label_source=label_source, horizon=horizon)
    eligible = sum(r.completed_sessions for r in rows)
    covered = sum(r.verdict_covered for r in rows)
    if any(r.chain != "no_chain" for r in rows):
        chain_m = rate(
            sum(r.chain == "verified" for r in rows), len(rows),
            eligible=len(rows), censored=0,
            label_source=label_source, horizon=horizon)
    else:
        chain_m = Measurement(
            estimate=NoEstimate("ledger has no chain"),
            n=0, eligible=len(rows), censored=0, coverage=0.0,
            label_source=label_source, horizon=horizon)
    rec_elig = sum(r.spend_available for r in rows)
    rec_n = sum(r.spend_facts for r in rows)
    rec = (rate(rec_n, rec_elig, eligible=rec_elig, censored=0,
                label_source=label_source, horizon=horizon) if rec_n else
           Measurement(estimate=NoEstimate("no session_cost facts"), n=0,
                       eligible=rec_elig, censored=0, coverage=0.0,
                       label_source=label_source, horizon=horizon))
    fid_rows = tuple(r for r in rows if r.ceiling_held is not None)
    fid = (rate(sum(1 for r in fid_rows if r.ceiling_held), len(fid_rows),
                eligible=len(fid_rows), censored=0,
                label_source=label_source, horizon=horizon) if fid_rows else
           Measurement(estimate=NoEstimate("no spend-ceiling runs"), n=0,
                       eligible=0, censored=0, coverage=0.0,
                       label_source=label_source, horizon=horizon))
    return Scorecard(
        scenarios=rows,
        miss_rate=rate(
            misses, len(observed_req), eligible=len(required),
            censored=len(required) - len(observed_req),
            label_source=label_source, horizon=horizon),
        false_alarm_rate=rate(
            alarms, len(observed_ctl), eligible=len(controls),
            censored=len(controls) - len(observed_ctl),
            label_source=label_source, horizon=horizon),
        detection_latency=lat,
        verdict_in_ledger_coverage=rate(
            covered, eligible, eligible=eligible, censored=0,
            label_source=label_source, horizon=horizon),
        ledger_chain_verified=chain_m,
        spend_recorded_coverage=rec,
        spend_ceiling_fidelity=fid,
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
        shown = "INVALID" if row.outcome == INVALID else row.outcome
        if row.outcome == INVALID:
            extra = f" error={row.error}" if row.error else ""
        elif row.outcome == DETECTION and row.latency.polls is not None:
            extra = f" latency {row.latency.polls} polls"
        elif row.latency.reason:
            extra = f" NoEstimate ({row.latency.reason})"
        elif row.latency.censored:
            extra = f" censored at {row.latency.bound_polls} polls"
        lines.append(f"  {row.name}: {shown} exit={row.exit_code}{extra}")
    lines.append(format_measurement("miss_rate", card.miss_rate))
    lines.append(format_measurement("false_alarm_rate", card.false_alarm_rate))
    lines.append(format_measurement("detection_latency", card.detection_latency))
    lines.append(format_measurement(
        "verdict_in_ledger_coverage", card.verdict_in_ledger_coverage))
    lines.append(format_measurement(
        "ledger_chain_verified", card.ledger_chain_verified))
    lines.append(format_measurement(
        "spend_recorded_coverage", card.spend_recorded_coverage))
    lines.append(format_measurement("spend_ceiling_fidelity",
                                    card.spend_ceiling_fidelity))
    return "\n".join(lines)


def _num(value: float) -> str:
    if math.isfinite(value) and value == int(value) and abs(value) < 10**12:
        return str(int(value))
    text = f"{value:.4f}"
    return text.rstrip("0").rstrip(".") if "." in text else text
