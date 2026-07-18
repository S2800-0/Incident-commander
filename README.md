# Incident Commander

A multi-agent SRE system that performs **active differential diagnosis** on production
incidents.

The one idea: when competing root-cause hypotheses are close in likelihood, the system
does **not** guess and does **not** just show both. It identifies the single unobserved
signal that would settle the argument, fetches exactly that signal (a **probe**),
eliminates a hypothesis, reports a **calibrated** confidence, and seals its reasoning
into a **cryptographically verifiable** artifact.

The proof is an **ablation**: run each labeled incident twice — probes **ON** vs **OFF**.

```
★ HEADLINE — top-1 accuracy on {ambiguous, adversarial_redherring}
    probes ON : 100%
    probes OFF:   0%
    Δ         : +100%

False-positive rollback (adversarial):  OFF 100%  →  ON 0%
Brier score (lower better):             OFF 0.362 →  ON 0.004
```

> Numbers are illustrative, not publication-grade. The headline is the
> **sign and size of the delta**, reproducible offline in one command.

**VoI framing.** This prototype implements the Value-of-Information decision policy over
the observation half of the mixed action space specified in our design document. The
intervention primitive is defined and safety-bounded; its live execution is Phase 2 of
our roadmap. What runs here is the mechanism that decides when an intervention would be
justified — which is the research contribution. Every candidate next action carries a
computed VoI score (EIG − λ·cost − μ·risk); the selector picks argmax over executable
actions, and the console's VoI panel shows the whole ranking live — including why the
intervention *would* have been chosen if it were executable.

---

## Quickstart

```bash
pip install -e .                 # pydantic, cryptography, fastapi, uvicorn

# 1. The proof — run the ablation
python -m ic.harness             # prints the table, writes harness_results.json

# 2. The demo path — seal a postmortem, then verify it offline
python -m ic.investigate INC-4471 -o postmortem.json
python verify.py postmortem.json          # → VERIFIED ✅
#   edit one evidence byte …
python verify.py postmortem_tampered.json # → TAMPERED ❌

# 3. The console
python -m server.app             # records replays/  (one-time)
uvicorn server.app:app --port 8000
cd console && npm install && npm run dev  # → http://localhost:5173
```

Open the console, pick `INC-4471`, hit **Investigate**: two agents disagree, ambiguity
trips, `P_conn_wait` is selected, H2 collapses, H1 lands ~94%, the evidence chain seals.
The **Ablation & Calibration** tab renders the delta bars and reliability diagram.

---

## How it works

```
triage ── seeds the competing hypotheses (H1 vs H2)
   │
   ├─ change_agent    (deploy-biased)   ─┐
   ├─ telemetry_agent (signal-biased)   ─┤ each scores the hypotheses it owns
   └─ history_agent   (pattern-biased)  ─┘ from prior evidence only
   │
adjudicator ── softmax → posteriors → top-2 margin
   │            margin < τ  OR  leader wants an irreversible rollback?
   │                 │ yes
   ▼                 ▼
conclude        probe loop:  select_probe (max info-gain / cost)
                             → run_probe (the ONLY path to the discriminator)
                             → re-score (confirm ↑ / contradict ↓ & eliminate)
                             → back to adjudicator   (bounded K=4, T=45s, τ=0.15)
   │
verdict → Merkle-root the cited evidence → Ed25519-sign → postmortem.json
```

**The invariant (load-bearing):** the discriminating signal lives only in
`evidence.probeable`, reachable solely through `Bundle.run_probe`. Setting
`probes_enabled=False` makes that path dead — that is the ablation switch, and it
genuinely removes the discriminator from every agent's context. That is why probes-off
scores 0% on the hard slices; if it scored 100%, the discriminator would have leaked
into `prior` (see `schema/incident_bundle.schema.md`).

**Probe selection is the contribution** (`ic/probe.py`): divergence over an observable =
number of *distinct* predictions the surviving hypotheses make about it; a probe's
info-gain = max divergence over the observables it measures; the selector picks
`argmax(info_gain / cost_ms)`. On INC-4471 it correctly prefers `P_conn_wait`
(divergence 2) over the plausible-but-useless `P_inv_errors` (divergence 0).

**Deterministic engine vs live LLM.** The offline default is a deterministic analytical
reasoner (`ic/reasoner.py`) that derives every number from the evidence payloads
themselves — so the ablation is reproducible and gradeable with no API key. The
mechanism is identical with a live model.

### Running the agents on a real model (OpenRouter free tier → Claude)

```bash
pip install -e ".[llm]"          # anthropic SDK + python-dotenv
cp .env.example .env             # then paste a real key and set IC_USE_LLM=1

# Free-tier iteration key from https://openrouter.ai  (Anthropic-compatible endpoint)
#   OPENROUTER_API_KEY=sk-or-v1-...   IC_PROVIDER=openrouter_free

# Step 0 — do the free models even keep the JSON contract? Run this FIRST:
python test_json_contract.py                 # 10/10 → build on it; <7/10 → switch provider

# Then any live investigation uses the model for agent reasoning:
IC_USE_LLM=1 python -m ic.investigate INC-4471
```

Everything routes through one choke point, `ic/llm.py::json_call(system, user, schema)`:
strict JSON validated against a Pydantic model, **one retry, then failover down a
provider chain** (`openrouter_free → openrouter_pinned → anthropic`) so a flaky free
model can't corrupt a run. Swapping providers is just `IC_PROVIDER` in `.env` — no agent
code changes. Iterate free on OpenRouter, pin one free slug for reproducible numbers, do
the final quality pass on Claude.

Two boundaries keep it honest:

- **The harness always runs deterministic** (`use_llm=False`, hard-coded) — a per-call
  auto-router would make Brier/accuracy a grab-bag. The graded artifact stays reproducible.
- **The model only proposes *prior* support.** Probe re-scoring stays deterministic and
  data-grounded — a weak free model must never override a *measured* signal. On any model
  failure the agent falls back to the analytical lens, so an investigation never breaks.

> `base_url` gotcha (fixed in `ic/llm.py`): the Anthropic SDK appends `/v1/messages`, so
> OpenRouter's base must be `https://openrouter.ai/api` (→ `/api/v1/messages`). The
> intuitive `.../api/v1` doubles to `/api/v1/v1/messages` and 404s.

---

## Guardrails (enforced in code, not just prompts)

- Every hypothesis must cite ≥1 resolvable evidence ref, or its posterior is penalised
  (`orchestrator.py`, anti-hallucination gate).
- The **adjudicator has no tool access** — it only reconciles structured hypotheses, so
  evidence text can bias an agent but can never trigger an action.
- Retrieved evidence enters LLM context as delimited, typed, hashed data
  (`llm.evidence_block`), never in an instruction position.
- The probe loop is hard-bounded: `K_MAX_PROBES=4`, `T_WALL_SECONDS=45`, `TAU=0.15`.
- The only state-changing action (`rollback`) is **gated**: a `gate_pending` event is
  emitted and it is never auto-fired.

---

## Layout

```
corpus/         provided frozen incidents (INC-4471/4472/4473)
schema/         the bundle schema (read the "one invariant" section)
ic/             models, bundle+probe gate, reasoner, agents, adjudicator, probe,
                orchestrator, evidence_chain, harness, llm, investigate CLI
server/app.py   FastAPI + WebSocket event stream (+ replay recorder)
console/        Vite + React + TS single-screen console (Recharts)
verify.py       standalone offline evidence-chain verifier
```

## Explicitly out of scope (stubbed, by design)

Real Datadog/Splunk/Jira connectors, SSO/SCIM/RBAC, multi-tenancy, billing, HA, k8s.
"Vector search" over prior incidents is a keyword match against the corpus, not pgvector.
These score nothing here and are noted as stubs in code comments.
