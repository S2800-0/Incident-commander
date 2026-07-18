"""Analytical reasoning engine — the shared, deterministic primitives the agents
and adjudicator are thin lenses over.

Why deterministic: the ablation (probes-on vs probes-off) has to be *gradeable* and
reproducible offline. Every number here is derived from the actual evidence payloads
(time series, maps), never hard-coded per incident — a new well-authored bundle in the
same observable families would resolve the same way. When IC_USE_LLM=1 and a key is
present, `ic/llm.py` can substitute genuine model calls; the mechanism is identical.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from .models import EvidenceItem, Hypothesis

# --- tunable constants (all module-level, per the guardrails) --------------
MATCH_BONUS = 1.6      # log-odds added when a probe confirms a prediction
MISMATCH_PENALTY = -2.6  # log-odds when a probe contradicts a prediction
DEPLOY_PRIOR = 0.55
UPSTREAM_PRIOR = 0.50
UPSTREAM_EARLY_BONUS = 0.45  # upstream signal that leads the alert → strong cascade prior
RESOURCE_PRIOR = 0.50
CAPACITY_PRIOR = 0.60
HISTORY_PRIOR = 0.35
POSTERIOR_CAP = 0.94   # never claim certainty — keeps confidence honest/calibrated
POSTERIOR_FLOOR = 0.02
DEPLOY_RECENT_MIN = 15  # minutes: a deploy within this window of the alert is "recent"


# --- time / series helpers -------------------------------------------------

def parse_ts(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


@dataclass
class Onset:
    ts: Optional[datetime]
    sustained: bool
    magnitude: float  # peak / baseline


def series_onset(series: list, jump: float = 1.5, sustain_ratio: float = 1.3) -> Onset:
    """First timestamp a series rises >= `jump`x its baseline, plus whether it stays
    elevated (sustained) or recovers (transient). Transient blips are real but do not
    count as 'leading' a cascade — this is how a benign warm-up is discounted."""
    pts = [(parse_ts(t), float(v)) for t, v in series]
    if not pts:
        return Onset(None, False, 0.0)
    base = max(pts[0][1], 1e-6)
    onset = None
    for ts, v in pts:
        if v >= base * jump and v > pts[0][1]:
            onset = ts
            break
    if onset is None:
        return Onset(None, False, max(v for _, v in pts) / base)
    last = pts[-1][1]
    sustained = last >= base * sustain_ratio
    return Onset(onset, sustained, max(v for _, v in pts) / base)


def _numbers_from_map(m: dict) -> list[float]:
    out = []
    for v in m.values():
        if isinstance(v, (int, float)):
            out.append(float(v))
        elif isinstance(v, str):
            mm = re.search(r"-?\d+(?:\.\d+)?", v)
            # only accept strings that are essentially a bare number, not "even (~195 rps)"
            if mm and re.fullmatch(r"\s*-?\d+(?:\.\d+)?\s*", v):
                out.append(float(mm.group()))
    return out


# --- observed-label interpreters (dispatch by prediction vocabulary) -------
# Each maps a probe result payload to the qualitative label the hypotheses argue about.

def interpret_ordering(probe_item: EvidenceItem, session: list[EvidenceItem]) -> str:
    """rises_first vs rises_later: does the probed signal *sustainedly* lead every
    other sustained signal in the session?"""
    mine = series_onset(probe_item.payload.get("series", []))
    if not mine.ts or not mine.sustained:
        return "rises_later"
    earliest_other = None
    for it in session:
        if it.source_uri == probe_item.source_uri:
            continue
        s = it.payload.get("series")
        if not s:
            continue
        o = series_onset(s)
        if o.ts and o.sustained:
            if earliest_other is None or o.ts < earliest_other:
                earliest_other = o.ts
    if earliest_other is None or mine.ts <= earliest_other:
        return "rises_first"
    return "rises_later"


def interpret_threshold(probe_item: EvidenceItem, fail_threshold: float = 2.0) -> str:
    """stays_healthy vs also_failing: did the sibling signal actually break?"""
    series = probe_item.payload.get("series", [])
    if not series:
        return "stays_healthy"
    mx = max(float(v) for _, v in series)
    return "also_failing" if mx >= fail_threshold else "stays_healthy"


def interpret_dispersion(probe_item: EvidenceItem, ratio_threshold: float = 2.0) -> str:
    """old_pods_slower vs uniform_across_pods: is there a large spread across cohorts?"""
    vals = _numbers_from_map(probe_item.payload.get("map", {}))
    if len(vals) < 2:
        return "uniform_across_pods"
    ratio = max(vals) / max(min(vals), 1e-9)
    return "old_pods_slower" if ratio >= ratio_threshold else "uniform_across_pods"


ORDERING_VOCAB = {"rises_first", "rises_later"}
THRESHOLD_VOCAB = {"stays_healthy", "also_failing"}
DISPERSION_VOCAB = {"old_pods_slower", "uniform_across_pods", "uniform"}


def observed_label(observable: str, predictions: set[str],
                   probe_item: EvidenceItem, session: list[EvidenceItem]) -> Optional[str]:
    """Dispatch to the interpreter matching the vocabulary the hypotheses disagree in."""
    if predictions & ORDERING_VOCAB:
        return interpret_ordering(probe_item, session)
    if predictions & THRESHOLD_VOCAB:
        return interpret_threshold(probe_item)
    if predictions & DISPERSION_VOCAB:
        return interpret_dispersion(probe_item)
    return None


# --- hypothesis classification (lens selection) ----------------------------

@dataclass
class Kind:
    deploy_blame: bool = False
    upstream: bool = False
    resource_leak: bool = False
    capacity: bool = False


_DEPLOY_NEG = ["regardless", "unrelated", "innocent", "not related", "despite"]
# Note: intentionally NOT "commit" — it false-matches DB terms like "commit backpressure"
# / "commit latency". Deploy identity comes from "deploy"/"config"/"rollback" instead.
_DEPLOY_REF = ["deploy", "config change", "config", "pool"]
_DEPLOY_CAUSAL = ["broke", "broken", "caused", "causing", "reduced", "introduced",
                  "contention", "misconfig", "exhaust", "roll it back"]
_ROLLBACK_VERB = ["roll back", "rollback", "revert", "roll it back"]


def _deploy_blame(c: str) -> bool:
    """A hypothesis blames a change only if it actually attributes the fault to a
    deploy/config (or asks to roll it back) — not if it merely *mentions* the deploy
    to exonerate it ('...regardless of deploy state')."""
    if any(neg in c for neg in _DEPLOY_NEG):
        return False
    rollback_verb = any(k in c for k in _ROLLBACK_VERB)
    deploy_ref = any(k in c for k in _DEPLOY_REF)
    causal = any(k in c for k in _DEPLOY_CAUSAL)
    return rollback_verb or (deploy_ref and causal)


def classify(claim: str) -> Kind:
    c = claim.lower()
    upstream = any(k in c for k in ["upstream", "cascade", "downstream", "dependency",
                                    "gateway", "degraded", "caller", "external"])
    resource = any(k in c for k in ["leak", "heap", "gc ", "memory", "uptime"])
    capacity = any(k in c for k in ["scale", "capacity", "traffic", "surge"])
    return Kind(deploy_blame=_deploy_blame(c), upstream=upstream,
                resource_leak=resource, capacity=capacity and not resource)


def is_rollback_hypothesis(claim: str) -> bool:
    """Does concluding this hypothesis mean 'roll back the recent deploy'?"""
    return classify(claim).deploy_blame


# --- prior-evidence support signals ----------------------------------------

def recent_deploy(evidence: list[EvidenceItem], alert: dict) -> Optional[tuple[EvidenceItem, float]]:
    fired = alert.get("fired_at")
    for e in evidence:
        if e.source_type == "deployment":
            dep = e.payload.get("deployed_at")
            if dep and fired:
                mins = (parse_ts(fired) - parse_ts(dep)).total_seconds() / 60.0
                return (e, mins)
    return None


def earliest_upstream_signal(evidence: list[EvidenceItem], alert: dict
                             ) -> Optional[tuple[EvidenceItem, bool]]:
    """A neighbouring service's metric that moved. Returns (item, before_alert)."""
    svc = alert.get("service", "")
    fired = parse_ts(alert["fired_at"]) if alert.get("fired_at") else None
    best = None
    for e in evidence:
        if e.source_type != "metric":
            continue
        # skip the primary service's own signal — that's the symptom, not an upstream lead
        if svc and f"//{svc}/" in e.source_uri:
            continue
        o = series_onset(e.payload.get("series", []))
        if o.ts is None:
            continue
        if best is None or o.ts < best[1]:
            best = (e, o.ts)
    if best is None:
        return None
    before = bool(fired and best[1] < fired)
    return (best[0], before)


def resource_trend_up(evidence: list[EvidenceItem]) -> Optional[EvidenceItem]:
    for e in evidence:
        if e.source_type == "metric" and any(k in e.source_uri for k in ["heap", "memory", "mem_"]):
            o = series_onset(e.payload.get("series", []), jump=1.3)
            if o.ts:
                return e
    return None


def traffic_trend_up(evidence: list[EvidenceItem]) -> Optional[EvidenceItem]:
    for e in evidence:
        if e.source_type == "metric" and any(k in e.source_uri for k in ["request_rate", "rps", "throughput"]):
            o = series_onset(e.payload.get("series", []), jump=1.1)
            if o.ts:
                return e
    return None


# Generic infra/plumbing words are not diagnostic signatures — a runbook and an
# unrelated hypothesis both mention "service"/"checkout". History matches on the
# *mechanism* (pool, connection, leak, gateway, cascade), never on service names.
_STOP = {"the", "and", "a", "an", "of", "to", "in", "on", "under", "into", "that",
         "first", "load", "it", "is", "this", "with", "for", "roll", "back",
         "service", "services", "checkout", "request", "requests", "error", "errors",
         "rate", "alert", "spike", "production", "prod", "then", "than", "from",
         "caused", "cause", "causing", "downstream", "upstream"}


def history_match(hyp: Hypothesis, evidence: list[EvidenceItem]) -> Optional[EvidenceItem]:
    """Does a prior-incident/runbook signature share salient terms with the claim?"""
    claim_terms = {w for w in re.findall(r"[a-z]+", hyp.claim.lower())
                   if len(w) > 3 and w not in _STOP}
    for e in evidence:
        if e.source_type in ("prior_incident", "runbook"):
            blob = " ".join(str(v) for v in e.payload.values()).lower()
            blob_terms = set(re.findall(r"[a-z]+", blob))
            if len(claim_terms & blob_terms) >= 2:
                return e
    return None


# --- scoring ---------------------------------------------------------------

def softmax(logits: dict[str, float]) -> dict[str, float]:
    if not logits:
        return {}
    mx = max(logits.values())
    exp = {k: math.exp(v - mx) for k, v in logits.items()}
    z = sum(exp.values())
    return {k: v / z for k, v in exp.items()}


def clamp_posterior(p: float) -> float:
    return max(POSTERIOR_FLOOR, min(POSTERIOR_CAP, p))
