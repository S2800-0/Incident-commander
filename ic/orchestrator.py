"""Orchestrator — runs a full investigation and emits structured events for the console.

Event types (all dicts with a `type` key):
  agent_started, hypothesis_proposed, posterior_updated, ambiguity_detected,
  probe_selected, probe_result, hypothesis_eliminated, exhausted, gate_pending,
  verdict, chain_sealed
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable, Optional

from .adjudicator import (TAU, apply_probe_result, decide, ranked, reconcile,
                          top_margin)
from .agents import Contribution, change_agent, history_agent, telemetry_agent, triage
from .agents.llm_lens import llm_contributions
from .bundle import Bundle
from .evidence_chain import seal
from .llm import llm_enabled
from .models import EvidenceItem, Hypothesis, Probe, Verdict
from .probe import select_probe
from .reasoner import is_rollback_hypothesis
from .voi import (STAGNATION_THRESHOLD, intervention_would_fire,
                  observation_stagnated, score_actions)

# Hard probe-loop bounds (guardrail).
K_MAX_PROBES = 4
T_WALL_SECONDS = 45
NO_EVIDENCE_PENALTY = 0.5  # an uncited claim can't win on nothing

Emit = Callable[[dict], None]


@dataclass
class RunResult:
    verdict: Verdict
    events: list[dict] = field(default_factory=list)
    probes_run: list[str] = field(default_factory=list)
    interventions_run: list[str] = field(default_factory=list)
    time_to_conclusion_s: float = 0.0
    sealed: dict = field(default_factory=dict)
    cited_items: list[EvidenceItem] = field(default_factory=list)


def _noop(_e: dict) -> None:
    pass


def run_investigation(bundle: Bundle, probes_enabled: bool = True,
                      emit: Optional[Emit] = None, pacing: float = 0.0,
                      use_llm: Optional[bool] = None) -> RunResult:
    # use_llm=None → auto-detect from env (IC_USE_LLM + a real key). The harness passes
    # use_llm=False explicitly so ablation numbers are always deterministic/reproducible.
    if use_llm is None:
        use_llm = llm_enabled()
    emit = emit or _noop
    events: list[dict] = []

    def _emit(e: dict) -> None:
        e = {"incident_id": bundle.incident_id, "probes_enabled": probes_enabled, **e}
        events.append(e)
        emit(e)
        if pacing:
            time.sleep(pacing)

    t0 = time.time()

    # --- [1] triage: seed the competing hypotheses --------------------------
    hyps: list[Hypothesis] = triage.seed_hypotheses(bundle)
    session: list[EvidenceItem] = bundle.prior_evidence()
    resolvable = {e.ref() for e in session}
    logits: dict[str, float] = {h.id: 0.0 for h in hyps}

    # --- [2] parallel fan-out: each agent scores the hypotheses it owns -----
    for mod in (change_agent, telemetry_agent, history_agent):
        _emit({"type": "agent_started", "agent_id": mod.AGENT_ID,
               "mode": "llm" if use_llm else "analytical"})
        contribs: list[Contribution] = []
        if use_llm:
            try:
                contribs = llm_contributions(mod.AGENT_ID, bundle, session, hyps)
            except Exception as e:
                # a flaky/keyless model must never break an investigation
                _emit({"type": "agent_fallback", "agent_id": mod.AGENT_ID,
                       "reason": str(e).splitlines()[0][:120]})
                contribs = []
        if not contribs:
            contribs = mod.assess(bundle, session, hyps)
        for c in contribs:
            # anti-hallucination gate: only credit refs that resolve to real evidence.
            good = [r for r in c.evidence_refs if r in resolvable]
            logits[c.hyp_id] = logits.get(c.hyp_id, 0.0) + c.logit_delta
            h = next(h for h in hyps if h.id == c.hyp_id)
            for r in good:
                if r not in h.evidence_refs:
                    h.evidence_refs.append(r)
            _emit({"type": "agent_reasoned", "agent_id": c.agent_id,
                   "hyp_id": c.hyp_id, "delta": round(c.logit_delta, 3),
                   "rationale": c.rationale, "evidence_refs": good})

    # guardrail: a claim citing zero resolvable evidence is penalised.
    for h in hyps:
        if not h.evidence_refs:
            logits[h.id] -= NO_EVIDENCE_PENALTY

    reconcile(hyps, logits)
    for h in hyps:
        _emit({"type": "hypothesis_proposed", "id": h.id, "claim": h.claim,
               "agent_id": h.agent_id, "posterior": round(h.posterior, 3),
               "evidence_refs": h.evidence_refs})

    # --- [3–5] probe loop ---------------------------------------------------
    # The T_WALL budget bounds the PROBE loop, not the whole investigation — otherwise
    # slow live-LLM agent fan-out (which precedes this) would eat the budget and the loop
    # would "exhaust" before running a single probe. Time from here.
    probe_loop_t0 = time.time()
    probes_run: list[str] = []
    interventions_run: list[str] = []
    already = set()
    intervention_done = False
    voi_step = 0
    while True:
        # --- VoI overlay: decompose & rank every candidate next action -------
        voi_step += 1
        actions = score_actions(hyps, bundle.probe_catalog, already)
        _emit({"type": "voi_scored", "step": voi_step,
               "actions": [a.model_dump(mode="json") for a in actions]})

        stagnated = observation_stagnated(actions)
        interv_action = next((a for a in actions if not a.executable), None)
        # An intervention is EXECUTABLE for THIS incident only when the bundle
        # authored an intervention_result for it (i.e., the safety envelope /
        # simulation of causal outcome exists). Same simulation model as our probes.
        interv_available = bool(
            interv_action and probes_enabled and bundle.intervention_ids()
            and interv_action.action_id in bundle.intervention_ids()
        )

        if stagnated:
            obs = [a for a in actions if a.executable]
            best_obs = max(obs, key=lambda a: a.eig, default=None)
            _emit({"type": "voi_stagnation_detected",
                   "threshold": STAGNATION_THRESHOLD,
                   "best_observation": best_obs.action_id if best_obs else None,
                   "best_observation_eig": best_obs.eig if best_obs else 0.0,
                   "intervention_eig": interv_action.eig if interv_action else None,
                   "intervention_available": interv_available})

        # ---- Intervention execution path ---------------------------------
        # Runs when: observation has stagnated, the bundle authored an
        # intervention outcome, and we haven't already run one. The "execution"
        # is against fixture data (same simulation model as probes); the human
        # gate is emitted so the console can pause and require an Approve click.
        if stagnated and interv_available and not intervention_done:
            interv_id = interv_action.action_id
            _emit({"type": "gate_pending", "action": "intervention",
                   "intervention_id": interv_id,
                   "description": interv_action.description,
                   "safety_envelope": interv_action.safety_envelope,
                   "note": "human approval required before intervention executes — "
                           "console pauses here until Approve is clicked"})

            item = bundle.run_intervention(interv_id, probes_enabled=True)
            session.append(item)
            resolvable.add(item.ref())
            interventions_run.append(interv_id)
            intervention_done = True

            synth_probe = Probe(probe_id=interv_id, connector="intervention",
                                measures=list(item.measures), cost_ms=0,
                                load_class="read_light",
                                description=interv_action.description)
            outcomes = apply_probe_result(hyps, logits, synth_probe, item, session)
            observed = dict.fromkeys(f"{o.observable}={o.observed}" for o in outcomes)
            _emit({"type": "intervention_executed", "intervention_id": interv_id,
                   "source_uri": item.source_uri, "hash": item.content_hash()[:12],
                   "summary": "; ".join(observed)})
            for o in outcomes:
                h = next(h for h in hyps if h.id == o.hyp_id)
                if o.matched and item.ref() not in h.evidence_refs:
                    h.evidence_refs.append(item.ref())
                _emit({"type": "posterior_updated", "id": o.hyp_id,
                       "posterior": round(h.posterior, 3),
                       "matched": o.matched, "observable": o.observable,
                       "source": "intervention"})
                if h.eliminated:
                    _emit({"type": "hypothesis_eliminated", "id": h.id,
                           "reason": h.eliminated_reason,
                           "source": "intervention"})
            continue  # re-score; loop will conclude next iteration

        if intervention_would_fire(actions) and not interv_available:
            # Honest failure mode: intervention is the argmax over all actions
            # but this bundle didn't author an outcome → we can't execute.
            _emit({"type": "intervention_would_fire",
                   "action_id": interv_action.action_id if interv_action else None,
                   "safety_envelope": interv_action.safety_envelope if interv_action else None,
                   "unavailable_reason": "not_available_in_prototype"})

        decision = decide(hyps, bundle.probe_catalog, already)
        if decision.action == "conclude":
            break
        if not probes_enabled:
            # ablation-off arm: we can *see* the ambiguity but cannot resolve it.
            _emit({"type": "ambiguity_detected", "margin": round(decision.margin, 3),
                   "tau": TAU, "resolvable": False,
                   "note": "probes disabled — concluding on prior evidence only"})
            break
        if len(probes_run) >= K_MAX_PROBES or (time.time() - probe_loop_t0) > T_WALL_SECONDS:
            nxt = select_probe(bundle.probe_catalog, hyps, already)
            _emit({"type": "exhausted", "margin": round(decision.margin, 3),
                   "would_run_next": nxt.probe.probe_id if nxt else None,
                   "surviving": [h.id for h in ranked(hyps)]})
            break

        _emit({"type": "ambiguity_detected", "margin": round(decision.margin, 3),
               "tau": TAU, "resolvable": True, "reason": decision.reason})

        choice = decision.probe_choice
        _emit({"type": "probe_selected", "probe_id": choice.probe.probe_id,
               "info_gain": choice.info_gain, "cost_ms": choice.probe.cost_ms,
               "description": choice.probe.description})

        item = bundle.run_probe(choice.probe.probe_id, probes_enabled=True)
        session.append(item)
        resolvable.add(item.ref())
        probes_run.append(choice.probe.probe_id)
        already.add(choice.probe.probe_id)

        outcomes = apply_probe_result(hyps, logits, choice.probe, item, session)
        observed = dict.fromkeys(f"{o.observable}={o.observed}" for o in outcomes)
        summary = "; ".join(observed)
        _emit({"type": "probe_result", "probe_id": choice.probe.probe_id,
               "source_uri": item.source_uri, "hash": item.content_hash()[:12],
               "summary": summary})

        for o in outcomes:
            h = next(h for h in hyps if h.id == o.hyp_id)
            if o.matched and item.ref() not in h.evidence_refs:
                h.evidence_refs.append(item.ref())
            _emit({"type": "posterior_updated", "id": o.hyp_id,
                   "posterior": round(h.posterior, 3),
                   "matched": o.matched, "observable": o.observable})
            if h.eliminated:
                _emit({"type": "hypothesis_eliminated", "id": h.id,
                       "reason": h.eliminated_reason})

    # --- verdict ------------------------------------------------------------
    winner = ranked(hyps)[0]
    rollback = is_rollback_hypothesis(winner.claim)
    used_refs = sorted({r for h in hyps for r in h.evidence_refs})
    verdict = Verdict(
        incident_id=bundle.incident_id,
        root_cause_id=winner.id,
        summary=winner.claim,
        posterior=round(winner.posterior, 3),
        rollback_recommended=rollback,
        hypotheses=hyps,
        probes_run=probes_run,
        evidence_refs=used_refs,
        provenance="interventional" if interventions_run else "observational",
    )

    _emit({"type": "provenance_labeled", "provenance": verdict.provenance,
           "interventions_run": interventions_run})

    if rollback:
        _emit({"type": "gate_pending", "action": "rollback",
               "note": "state-changing action requires explicit approval — not auto-fired"})

    # --- seal the evidence chain -------------------------------------------
    used_items = [e for e in session if e.ref() in set(used_refs)]
    sealed = seal(verdict, used_items)
    verdict.merkle_root = sealed["merkle_root"]
    verdict.signature = sealed["signature"]
    result_time = time.time() - t0
    _emit({"type": "verdict", "root_cause_id": verdict.root_cause_id,
           "summary": verdict.summary, "posterior": verdict.posterior,
           "rollback_recommended": verdict.rollback_recommended,
           "probes_run": probes_run})
    _emit({"type": "chain_sealed", "merkle_root": sealed["merkle_root"],
           "signature": sealed["signature"][:24] + "…",
           "leaf_count": len(used_items)})

    return RunResult(verdict=verdict, events=events, probes_run=probes_run,
                     interventions_run=interventions_run,
                     time_to_conclusion_s=round(result_time, 3),
                     sealed=sealed, cited_items=used_items)
