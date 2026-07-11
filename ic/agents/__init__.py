"""Investigation agents. Each is a biased lens; disagreement between them is the
mechanism, not a bug.

In offline mode agents score hypotheses with the deterministic analytical engine
(ic/reasoner.py). When IC_USE_LLM=1, they may additionally use the model to phrase
claims/predictions, but likelihoods are always derived from the evidence data so the
control flow stays reproducible.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from pydantic import BaseModel, Field


@dataclass
class Contribution:
    """One agent's assessment of one hypothesis."""
    hyp_id: str
    logit_delta: float
    evidence_refs: list[str] = field(default_factory=list)
    rationale: str = ""
    agent_id: str = ""


class AgentAssessment(BaseModel):
    """Strict JSON contract an agent returns in LLM mode (validated by ic.llm.json_call)."""
    favored_hypothesis_id: str = Field(..., description="id of the hypothesis this lens supports")
    confidence: float = Field(..., ge=0.0, le=1.0, description="0..1 support for that hypothesis")
    evidence_ids: list[str] = Field(default_factory=list,
                                    description="source_uri of each evidence item relied on")
    rationale: str = Field("", description="one sentence, from this agent's biased viewpoint")


# Each agent is a deliberately biased lens. The bias is the mechanism: disagreement
# between lenses is what creates the ambiguity a probe then resolves.
BIAS = {
    "change_agent": (
        "You are the CHANGE AGENT. You are deploy-biased: you look for a recent code or "
        "config change that could explain the signal, and you weigh temporal proximity of "
        "a deploy to the alert heavily. Favor the hypothesis that blames a change."
    ),
    "telemetry_agent": (
        "You are the TELEMETRY AGENT. You are signal-biased: you reason about what the "
        "golden signals and topology show irrespective of cause. If an upstream neighbour "
        "moved early you lean cascade; if a resource metric climbs you lean leak; if traffic "
        "rose you lean capacity. Favor the hypothesis the metrics most support."
    ),
    "history_agent": (
        "You are the HISTORY AGENT. You are pattern-biased: you match this incident against "
        "prior incidents and runbooks. Favor the hypothesis whose signature matches known "
        "history; if nothing matches, report low confidence."
    ),
}
