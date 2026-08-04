"""Bundle loader + the probe gate.

THE INVARIANT (schema §"one invariant that matters"): the discriminating signal
lives only in `evidence.probeable`, reachable *only* through `run_probe`. When
`PROBES_ENABLED` is False, `run_probe` is dead — this is the ablation switch, and it
must genuinely remove the discriminator from every agent's context.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from .models import EvidenceItem, Hypothesis, PredictedObservation, Probe


class Bundle:
    def __init__(self, raw: dict):
        self.raw = raw
        self.incident_id: str = raw["incident_id"]
        self.slice: str = raw.get("slice", "unknown")
        self.title: str = raw.get("title", "")
        self.alert: dict = raw.get("alert", {})
        self.topology: dict = raw.get("topology", {})
        self.observables: list[dict] = raw.get("observables", [])
        self.ground_truth: dict = raw.get("ground_truth", {})
        self.human_baseline_seconds: int = raw.get("human_baseline_seconds", 0)

        # Strip author-only annotation keys before they can ever reach an agent.
        self._prior = [self._clean(e) for e in raw["evidence"]["prior"]]
        self._probeable = [self._clean(e) for e in raw["evidence"]["probeable"]]
        self._probe_by_id = {e.probe_id: e for e in self._probeable}

        # Intervention results — same shape as probeable items, but reachable only
        # through run_intervention() and only when observation VoI has stagnated.
        # Bundles without an intervention_results tier are unchanged.
        self._intervention_results = [
            self._clean(e) for e in raw["evidence"].get("intervention_results", [])
        ]
        self._intervention_by_id = {e.probe_id: e for e in self._intervention_results}

        self.probe_catalog: list[Probe] = [Probe(**p) for p in raw.get("probe_catalog", [])]

        self.seed_hypotheses: list[Hypothesis] = []
        for h in raw.get("seed_hypotheses", []):
            self.seed_hypotheses.append(
                Hypothesis(
                    id=h["id"],
                    claim=h["claim"],
                    agent_id=h.get("natural_owner", "triage"),
                    predicted_observations=[
                        PredictedObservation(**po) for po in h.get("predicted_observations", [])
                    ],
                )
            )

    @staticmethod
    def _clean(e: dict) -> EvidenceItem:
        # Author notes (_note_for_authors, _*) must never enter agent context.
        payload = {k: v for k, v in e.items() if not k.startswith("_")}
        return EvidenceItem(
            source_type=payload["source_type"],
            source_uri=payload["source_uri"],
            retrieved_at=payload.get("retrieved_at"),
            measures=payload.get("measures", []),
            probe_id=payload.get("probe_id"),
            payload=payload.get("payload", {}),
        )

    # --- evidence access ---------------------------------------------------

    def prior_evidence(self) -> list[EvidenceItem]:
        """Always available: the cheap parallel fan-out."""
        return list(self._prior)

    def run_probe(self, probe_id: str, probes_enabled: bool = True) -> Optional[EvidenceItem]:
        """The ONLY path to probeable evidence. Gated by the ablation flag."""
        if not probes_enabled:
            raise RuntimeError(
                f"run_probe({probe_id}) called with PROBES_ENABLED=False — "
                "the probe loop must be skipped entirely in the ablation-off arm."
            )
        item = self._probe_by_id.get(probe_id)
        if item is None:
            raise KeyError(f"No probeable evidence for probe_id={probe_id}")
        return item

    def probe_ids(self) -> list[str]:
        return [p.probe_id for p in self.probe_catalog]

    def intervention_ids(self) -> list[str]:
        return list(self._intervention_by_id.keys())

    def run_intervention(self, intervention_id: str,
                         probes_enabled: bool = True) -> EvidenceItem:
        """Execute a bounded intervention against the fixture (same simulation model
        as our probes — we author the causal result). Gated by probes_enabled AND
        by the orchestrator only calling this after human approval AND observation
        stagnation. There is no way to reach this except through those gates."""
        if not probes_enabled:
            raise RuntimeError(
                f"run_intervention({intervention_id}) called with probes disabled")
        item = self._intervention_by_id.get(intervention_id)
        if item is None:
            raise KeyError(f"No intervention_result for {intervention_id}")
        return item

    def observable_units(self) -> dict[str, str]:
        return {o["name"]: o.get("unit", "") for o in self.observables}


def load_bundle(path: str | Path) -> Bundle:
    with open(path, "r", encoding="utf-8") as f:
        return Bundle(json.load(f))


def load_corpus(corpus_dir: str | Path = "corpus") -> list[Bundle]:
    corpus_dir = Path(corpus_dir)
    bundles = [load_bundle(p) for p in sorted(corpus_dir.glob("INC-*.json"))]
    return bundles


if __name__ == "__main__":
    # Step 1 runnable check.
    b = load_bundle("corpus/INC-4471.json")
    print(f"{b.incident_id} [{b.slice}] — {b.title}")
    print(f"  prior evidence items:     {len(b.prior_evidence())}")
    print(f"  probeable evidence items: {len(b._probeable)}")
    print(f"  probe catalog:            {b.probe_ids()}")
    # Prove the discriminator is NOT in prior context.
    prior_measures = {m for e in b.prior_evidence() for m in e.measures}
    print(f"  observables visible in prior: {sorted(prior_measures)}")
    print(f"  observables only via probe:   "
          f"{sorted({m for e in b._probeable for m in e.measures} - prior_measures)}")
