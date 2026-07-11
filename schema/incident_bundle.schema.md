# Incident Bundle Schema (v1.0)

A **bundle** is one production incident, frozen to disk, with its evidence and the
labeled ground-truth answer. Bundles are the inputs the agents consume in place of
live connectors, and they are what the Incident Replay Harness runs against.

---

## The one invariant that matters

Evidence is split into two tiers:

- **`evidence.prior`** — the cheap parallel fan-out (pipeline step [2]). The agents
  *always* see this. It is deliberately constructed so that **more than one hypothesis
  is plausible** from it alone. This is the ambiguity.

- **`evidence.probeable`** — fetched **only** when the probe loop (step [5]) requests
  it. The **single signal that discriminates between the competing hypotheses lives
  here, and nowhere in `prior`.**

> **Why this matters:** the ablation (probes-on vs probes-off) is only meaningful if
> turning probes off genuinely removes the discriminating signal. If the discriminator
> leaks into `prior`, both ablation arms see it, the delta collapses to zero, and the
> whole thesis becomes untestable. **When authoring a bundle, the last thing you check
> is: can either hypothesis be ruled out using `prior` alone? If yes, you have leaked
> the answer — move that signal into `probeable`.**

---

## Top-level structure

```jsonc
{
  "bundle_version": "1.0",
  "incident_id": "INC-4471",
  "slice": "ambiguous",            // see Slices below — the harness groups metrics by this
  "title": "Human-readable one-liner",

  "ground_truth": {
    "root_cause_id": "H1",         // which seeded hypothesis is correct
    "summary": "Plain-English true cause",
    "rollback_correct": true       // is 'roll back the recent deploy' the RIGHT call?
  },

  "human_baseline_seconds": 2100,  // logged human time-to-correct-hypothesis (for §12 reduction metric)

  "alert": { /* canonical Incident object — see below */ },
  "topology": { /* service dependency graph */ },

  "observables": [ /* controlled vocabulary of measurable quantities — see below */ ],

  "evidence": {
    "prior":     [ /* EvidenceItem[] — always fetched */ ],
    "probeable": [ /* EvidenceItem[] — fetched only on probe, keyed by probe_id */ ]
  },

  "probe_catalog": [ /* Probe[] — what the selector may choose from */ ]
}
```

---

## `alert` — canonical Incident object

```jsonc
{
  "service": "checkout-service",
  "environment": "production",
  "severity": "SEV-2",
  "fired_at": "2026-07-10T09:08:15Z",
  "signal": "http_5xx_rate",
  "condition": "error rate 12.4% > threshold 2%",
  "source": "pagerduty"
}
```

## `topology` — dependency graph

Directed edges `A -> B` mean "A calls B" (A is upstream of B's failure impact).
The Telemetry Agent traverses this to decide which neighbours to pull.

```jsonc
{
  "nodes": ["checkout-service", "orders-db", "inventory-service", "payment-gateway"],
  "edges": [
    { "from": "checkout-service", "to": "orders-db" },
    { "from": "checkout-service", "to": "inventory-service" },
    { "from": "checkout-service", "to": "payment-gateway" }
  ]
}
```

## `observables` — controlled vocabulary

The bridge that makes probe selection buildable. Each hypothesis an agent proposes
declares `predicted_observations` referencing these names. Each probe declares which
observables it `measures`. The selector picks the probe whose observables the surviving
hypotheses most *disagree* about. Keep names stable across bundles.

```jsonc
[
  { "name": "connection_wait_time",   "unit": "ms",  "desc": "time requests spend waiting for a DB connection" },
  { "name": "inventory_p99_latency",  "unit": "ms",  "desc": "upstream inventory-service p99" },
  { "name": "cpu_utilization",        "unit": "pct", "desc": "checkout-service pod CPU" },
  { "name": "error_type_breakdown",   "unit": "map", "desc": "counts of errors by exception class" },
  { "name": "sibling_service_errors", "unit": "pct", "desc": "error rate of a service sharing the dependency but NOT the deploy" }
]
```

---

## `EvidenceItem`

Every item is hashable: the system computes `content_hash` at retrieval from
`canonical_json(payload) || source_uri || retrieved_at`. Fixtures may include
`expected_hash` so the harness can self-check determinism (optional).

```jsonc
{
  "source_type": "metric",          // metric | log | deployment | diff | ticket | runbook | prior_incident | probe_result
  "source_uri": "prometheus://checkout-service/cpu",
  "retrieved_at": "2026-07-10T09:08:22Z",
  "measures": ["cpu_utilization"],  // which observables this item speaks to (optional for prior, required for probeable)
  "probe_id": null,                 // null in prior[]; the owning probe_id in probeable[]
  "payload": { /* type-specific, see below */ }
}
```

### Payload shapes by `source_type`

- **metric** — `{ "series": [["2026-07-10T09:00:00Z", 35.0], ...], "unit": "pct" }`
- **log** — `{ "lines": [{ "ts": "...", "level": "ERROR", "msg": "..." }, ...] }`
- **deployment** — `{ "version": "v2.14.0", "deployed_at": "...", "deployed_by": "...", "commit_sha": "..." }`
- **diff** — `{ "commit_sha": "...", "files": [{ "path": "...", "hunk": "- pool_size: 50\n+ pool_size: 10" }] }`
- **ticket** — `{ "key": "OPS-812", "title": "...", "status": "open", "body": "..." }`
- **runbook** — `{ "title": "...", "body": "..." }`
- **prior_incident** — `{ "incident_id": "INC-2209", "signature": "...", "resolution": "..." }`
- **probe_result** — same as metric/log/etc.; this is what a probe returns

---

## `Probe`

A probe is a discriminating query the selector may choose. In production these come
from a connector's `probe_templates()`. In a fixture they are declared here, and
"executing" a probe means reading `evidence.probeable[probe_id]`.

```jsonc
{
  "probe_id": "P_conn_wait",
  "connector": "prometheus",
  "measures": ["connection_wait_time"],
  "cost_ms": 410,                    // used as the denominator in info-gain / cost
  "load_class": "read_light",        // read_light | read_heavy | excluded  (heavy on a saturated svc is down-weighted)
  "description": "Connection-wait histogram for checkout-service, 08:55–09:10"
}
```

---

## Slices (the `slice` field)

Groups incidents for per-slice harness reporting. Author to these:

| slice                  | what it tests                                                        |
|------------------------|----------------------------------------------------------------------|
| `deployment_induced`   | the common case; deploy correlation works                            |
| `cascading_dependency` | topology traversal + probe selection                                 |
| `resource_exhaustion`  | slow-onset signals; hardest for threshold alerting                   |
| `ambiguous`            | **cheap evidence cannot decide — the probe-selection test set**      |
| `novel`                | zero prior-incident neighbours; tests confidence honesty             |
| `adversarial_redherring` | an innocuous deploy coincides with an unrelated cause; **tests false-positive rollback** |

---

## Authoring checklist (run this on every new bundle)

1. Does `prior` make **at least two** hypotheses genuinely plausible?
2. Is the **discriminating signal absent from `prior`** and present in exactly one
   `probeable` item?
3. Do the competing hypotheses make **opposing predictions** about at least one
   `observable` that a probe measures?
4. Are timestamps internally coherent (cause precedes effect)?
5. Is `ground_truth.rollback_correct` set honestly? (For red-herrings it must be `false`.)
6. Is `human_baseline_seconds` plausible for this incident class?
