"""History Agent — pattern-biased. Matches the incident against prior incidents and
runbooks. Stubbed 'vector search' is a keyword overlap against the corpus (no pgvector
for the prototype — see README)."""
from __future__ import annotations

from ..bundle import Bundle
from ..models import EvidenceItem, Hypothesis
from ..reasoner import HISTORY_PRIOR, history_match
from . import Contribution

AGENT_ID = "history_agent"


def assess(bundle: Bundle, evidence: list[EvidenceItem], hyps: list[Hypothesis]) -> list[Contribution]:
    out: list[Contribution] = []
    for h in hyps:
        match = history_match(h, evidence)
        if match is not None:
            sig = match.payload.get("signature") or match.payload.get("title") or match.source_uri
            out.append(Contribution(h.id, HISTORY_PRIOR, [match.ref()],
                                    f"matches known pattern: {sig}", AGENT_ID))
    return out
