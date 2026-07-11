"""Adjudicator — reconciles structured hypotheses and decides probe-or-conclude.

It has NO tool access (guardrail): it only reads hypotheses and the evidence already in
the session and compares predictions to observations. Evidence text can bias an agent
but can never trigger an action here.
"""
from __future__ import annotations

from dataclasses import dataclass

from .models import EvidenceItem, Hypothesis, Probe
from .probe import ProbeChoice, select_probe
from .reasoner import (MATCH_BONUS, MISMATCH_PENALTY, clamp_posterior,
                       is_rollback_hypothesis, observed_label, softmax)

TAU = 0.15  # top-2 posterior margin below which we call it ambiguous


@dataclass
class ProbeOutcome:
    hyp_id: str
    observable: str
    predicted: str
    observed: str
    matched: bool


def reconcile(hyps: list[Hypothesis], logits: dict[str, float]) -> None:
    """Softmax the current log-odds into posteriors (in place)."""
    live = {h.id: logits.get(h.id, 0.0) for h in hyps if not h.eliminated}
    probs = softmax(live)
    for h in hyps:
        if h.eliminated:
            h.posterior = clamp_posterior(0.02)
        else:
            h.posterior = clamp_posterior(probs.get(h.id, 0.0))


def ranked(hyps: list[Hypothesis]) -> list[Hypothesis]:
    live = [h for h in hyps if not h.eliminated]
    return sorted(live, key=lambda h: h.posterior, reverse=True)


def top_margin(hyps: list[Hypothesis]) -> float:
    r = ranked(hyps)
    if len(r) < 2:
        return 1.0
    return r[0].posterior - r[1].posterior


@dataclass
class Decision:
    action: str  # "conclude" | "probe"
    margin: float
    reason: str
    probe_choice: ProbeChoice | None = None


def decide(hyps: list[Hypothesis], catalog: list[Probe], already_run: set[str],
           tau: float = TAU) -> Decision:
    """Probe when the call is ambiguous, OR when the leader recommends an irreversible
    rollback and a cheap discriminating probe still exists (never roll back production
    on circumstantial evidence)."""
    margin = top_margin(hyps)
    leader = ranked(hyps)[0]
    choice = select_probe(catalog, hyps, already_run)

    if margin < tau and choice is not None:
        return Decision("probe", margin,
                        f"top-2 within {margin:.2f} < τ={tau} — fetch a discriminator", choice)
    if is_rollback_hypothesis(leader.claim) and choice is not None:
        return Decision("probe", margin,
                        "leader recommends rollback — verify with a discriminating probe "
                        "before an irreversible action", choice)
    return Decision("conclude", margin, f"margin {margin:.2f} ≥ τ={tau} — confident enough")


def apply_probe_result(hyps: list[Hypothesis], logits: dict[str, float], probe: Probe,
                       probe_item: EvidenceItem, session: list[EvidenceItem]) -> list[ProbeOutcome]:
    """Compare each surviving hypothesis's prediction for the probed observable against
    what the probe actually showed. Confirm → boost; contradict → collapse & eliminate."""
    outcomes: list[ProbeOutcome] = []
    for observable in probe.measures:
        predictors = {h.id: po.prediction for h in hyps if not h.eliminated
                      for po in h.predicted_observations if po.observable == observable}
        if not predictors:
            continue
        label = observed_label(observable, set(predictors.values()), probe_item, session)
        if label is None:
            continue
        for hid, pred in predictors.items():
            matched = (pred == label)
            logits[hid] = logits.get(hid, 0.0) + (MATCH_BONUS if matched else MISMATCH_PENALTY)
            if not matched:
                h = next(h for h in hyps if h.id == hid)
                h.eliminated = True
                h.eliminated_reason = (
                    f"probe showed {observable}={label!r}, but this hypothesis predicted "
                    f"{pred!r}")
            outcomes.append(ProbeOutcome(hid, observable, pred, label, matched))
    reconcile(hyps, logits)
    return outcomes
