"""Probe selection — the information-gain heuristic that is the project's contribution.

The selector picks the single unobserved signal whose result the surviving hypotheses
most *disagree* about, normalised by cost. It never sees `evidence.probeable`; it
reasons purely over the hypotheses' `predicted_observations` and the probe catalog.
"""
from __future__ import annotations

from dataclasses import dataclass

from .models import Hypothesis, Probe

EXCLUDED = "excluded"
HEAVY = "read_heavy"

# --- VoI weights (framing overlay; see ic/voi.py) --------------------------
# Not hyperparameters to tune — the demo's control surface. MU dominant encodes
# "prefer observation while it's informative; tolerate intervention only when
# observation has stagnated." That's the whole idea.
LAMBDA = 0.05   # cost weight — small; observations are cheap
MU = 1.00       # risk weight — dominant; makes intervention lose to observation
                # unless observation EIG is genuinely near zero


def divergence(observable: str, hyps: list[Hypothesis]) -> int:
    """Number of *distinct* predictions the surviving hypotheses make about an
    observable. 0 or 1 predictor ⇒ no argument to settle ⇒ divergence 0."""
    preds = [po.prediction for h in hyps if not h.eliminated
             for po in h.predicted_observations if po.observable == observable]
    return len(set(preds)) if len(preds) >= 2 else 0


def info_gain(probe: Probe, hyps: list[Hypothesis]) -> float:
    if probe.load_class == EXCLUDED:
        return 0.0
    gain = max((divergence(m, hyps) for m in probe.measures), default=0)
    if probe.load_class == HEAVY:
        gain *= 0.5  # heavy probe on an already-strained service is discouraged
    return float(gain)


@dataclass
class ProbeChoice:
    probe: Probe
    info_gain: float
    score: float  # info_gain / cost


def select_probe(catalog: list[Probe], hyps: list[Hypothesis],
                 already_run: set[str]) -> ProbeChoice | None:
    """argmax(info_gain / cost_ms) over probes not yet run with positive gain."""
    best: ProbeChoice | None = None
    for p in catalog:
        if p.probe_id in already_run:
            continue
        g = info_gain(p, hyps)
        if g <= 0:
            continue
        score = g / max(p.cost_ms, 1)
        if best is None or score > best.score:
            best = ProbeChoice(p, g, score)
    return best
