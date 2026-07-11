"""Change Agent — deploy-biased. Looks for a recent change that explains the signal.

It only sees deploy/diff/prior-incident style evidence and argues for whichever
hypothesis blames a change. Its edge comes from deploy↔alert temporal proximity.
"""
from __future__ import annotations

from ..bundle import Bundle
from ..models import EvidenceItem, Hypothesis
from ..reasoner import DEPLOY_PRIOR, DEPLOY_RECENT_MIN, classify, recent_deploy
from . import Contribution

AGENT_ID = "change_agent"


def assess(bundle: Bundle, evidence: list[EvidenceItem], hyps: list[Hypothesis]) -> list[Contribution]:
    dep = recent_deploy(evidence, bundle.alert)
    diff_refs = [e.ref() for e in evidence if e.source_type in ("deployment", "diff")]
    out: list[Contribution] = []
    for h in hyps:
        if not classify(h.claim).deploy_blame:
            continue
        if dep is None:
            continue
        item, mins = dep
        if mins <= DEPLOY_RECENT_MIN:
            delta = DEPLOY_PRIOR
            why = f"deploy landed {mins:.1f} min before the alert — a plausible cause"
        else:
            delta = 0.10
            why = f"deploy exists but {mins/60:.0f}h old — weak temporal link"
        out.append(Contribution(h.id, delta, diff_refs or [item.ref()], why, AGENT_ID))
    return out
