"""Triage — scope the incident and seed the hypothesis set.

For the prototype we adopt the bundle's seeded hypotheses (the competing stories the
downstream agents will fight over) and assign each to its natural owner.
"""
from __future__ import annotations

from ..bundle import Bundle
from ..models import Hypothesis


def seed_hypotheses(bundle: Bundle) -> list[Hypothesis]:
    seeds = [h.model_copy(deep=True) for h in bundle.seed_hypotheses]
    for h in seeds:
        h.posterior = 0.0
    return seeds
