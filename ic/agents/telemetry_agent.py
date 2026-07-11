"""Telemetry Agent — signal-biased. Reasons about what the metrics show irrespective
of cause, and proposes a cascade/upstream (or resource) story when the signals fit.

Its edge: an upstream neighbour that moved *before* the alert reads as a cascade; a
rising resource metric reads as a leak; rising traffic reads as a capacity limit.
"""
from __future__ import annotations

from ..bundle import Bundle
from ..models import EvidenceItem, Hypothesis
from ..reasoner import (CAPACITY_PRIOR, RESOURCE_PRIOR, UPSTREAM_EARLY_BONUS,
                        UPSTREAM_PRIOR, classify, earliest_upstream_signal,
                        resource_trend_up, traffic_trend_up)
from . import Contribution

AGENT_ID = "telemetry_agent"


def assess(bundle: Bundle, evidence: list[EvidenceItem], hyps: list[Hypothesis]) -> list[Contribution]:
    out: list[Contribution] = []
    upstream = earliest_upstream_signal(evidence, bundle.alert)
    resource = resource_trend_up(evidence)
    traffic = traffic_trend_up(evidence)

    for h in hyps:
        kind = classify(h.claim)
        if kind.upstream and upstream is not None:
            item, before = upstream
            delta = UPSTREAM_PRIOR + (UPSTREAM_EARLY_BONUS if before else 0.0)
            why = (f"upstream {item.source_uri.split('//')[-1]} moved"
                   + (" before the alert — reads as a cascade" if before else ""))
            out.append(Contribution(h.id, delta, [item.ref()], why, AGENT_ID))
        elif kind.resource_leak and resource is not None:
            out.append(Contribution(h.id, RESOURCE_PRIOR, [resource.ref()],
                                    "resource metric trending up — consistent with a leak", AGENT_ID))
        elif kind.capacity and traffic is not None:
            out.append(Contribution(h.id, CAPACITY_PRIOR, [traffic.ref()],
                                    "traffic rose — a capacity/scale-out story is plausible", AGENT_ID))
    return out
