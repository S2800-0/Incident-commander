"""Pydantic data model mirroring schema/incident_bundle.schema.md.

All hashing goes through `canonical_json` so hashes are byte-for-byte reproducible
across processes and machines. This is load-bearing for the evidence chain.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Optional

from pydantic import BaseModel, Field


def canonical_json(obj: Any) -> str:
    """Deterministic JSON encoding used everywhere a hash is computed."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_hex(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


class EvidenceItem(BaseModel):
    source_type: str
    source_uri: str
    retrieved_at: Optional[str] = None
    measures: list[str] = Field(default_factory=list)
    probe_id: Optional[str] = None
    payload: dict = Field(default_factory=dict)

    def content_hash(self) -> str:
        """sha256(canonical_json(payload) || source_uri || retrieved_at)."""
        basis = canonical_json(self.payload) + self.source_uri + str(self.retrieved_at)
        return sha256_hex(basis)

    def ref(self) -> str:
        """Stable short reference id used by agents to cite evidence."""
        return self.source_uri


class PredictedObservation(BaseModel):
    observable: str
    prediction: str
    note: str = ""


class Hypothesis(BaseModel):
    id: str
    claim: str
    agent_id: str
    posterior: float = 0.0
    predicted_observations: list[PredictedObservation] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    eliminated: bool = False
    eliminated_reason: str = ""


class Probe(BaseModel):
    probe_id: str
    connector: str
    measures: list[str] = Field(default_factory=list)
    cost_ms: int = 0
    load_class: str = "read_light"
    description: str = ""


class Verdict(BaseModel):
    incident_id: str
    root_cause_id: str
    summary: str
    posterior: float
    rollback_recommended: bool
    hypotheses: list[Hypothesis]
    probes_run: list[str]
    evidence_refs: list[str]
    merkle_root: Optional[str] = None
    signature: Optional[str] = None
