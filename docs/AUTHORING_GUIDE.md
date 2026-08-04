# Incident Commander — Bundle Authoring Guide

**Audience:** you (the observability intern joining the team).
**Time to productive:** ~2 hours if you follow this doc in order.
**Job to be done by end of Sprint 1:** author one incident bundle from a real
postmortem, end-to-end, and get it passing the harness.

> **The one thing to internalize before anything else.** This project is not a
> classifier. It's a decision policy. The corpus is not training data — it's a
> **benchmark** that measures whether the policy works. Every bundle you author is a
> deliberately-constructed exam question, not a raw incident dump. That framing
> changes everything about how you write them.

---

## Table of contents

1. [Repo tour — what lives where](#1-repo-tour)
2. [Environment setup (30 min)](#2-environment-setup)
3. [Running the harness — the daily loop](#3-running-the-harness)
4. [The invariant — read this twice](#4-the-invariant)
5. [Reading an existing bundle end-to-end](#5-reading-an-existing-bundle)
6. [The three interpreter types (this is the whole engine)](#6-the-three-interpreter-types)
7. [Authoring a bundle from a real postmortem — the 7-step pipeline](#7-authoring-a-bundle-the-pipeline)
8. [LLM-assisted first-draft workflow](#8-llm-assisted-workflow)
9. [Debug tree when the harness fails](#9-debug-tree)
10. [Where to find postmortems](#10-postmortem-sources)
11. [Common authoring mistakes (the "gotchas")](#11-common-mistakes)
12. [What NOT to change — the invariants of the invariants](#12-what-not-to-change)

---

## 1. Repo tour

You only need to know four folders. Everything else is scaffolding.

```
Incident commander/
├── corpus/                    # ← YOU LIVE HERE. Every bundle is one JSON file.
│   ├── INC-4471.json          # ambiguous (ordering)   — pool vs cascade
│   ├── INC-4472.json          # adversarial (sibling)  — dependency outage red herring
│   ├── INC-4473.json          # resource exhaustion    — leak vs traffic
│   ├── INC-4474.json          # cascade (ordering)     — real upstream lead
│   ├── INC-4475.json          # deployment-induced     — deploy is guilty
│   ├── INC-4476.json          # ambiguous DB           — lock storm vs replication
│   └── INC-4477.json          # adversarial DB         — noisy neighbor
├── schema/
│   └── incident_bundle.schema.md  # ← READ THIS. The invariant lives here.
├── ic/                        # engine. DO NOT MODIFY. If your bundle needs code
│   │                          # changes to work, the bundle is wrong — not the code.
│   ├── reasoner.py            # interprets probe results (three types, see §6)
│   ├── voi.py                 # value-of-information scoring
│   └── probe.py, adjudicator.py, orchestrator.py, ...
└── docs/
    └── AUTHORING_GUIDE.md     # this file
```

**Rule of thumb:** if you find yourself opening anything in `ic/`, stop and ask.
Your work lives in `corpus/` and nowhere else.

---

## 2. Environment setup

```bash
# 1. Clone (Shahy will invite you to the repo)
git clone <repo-url> "incident-commander"
cd "incident-commander"

# 2. Python side (needs Python 3.11+)
pip install -e ".[llm]"

# 3. Verify the engine runs
python -m ic.harness
```

**What you should see:**
```
INCIDENT COMMANDER — ABLATION HARNESS
Top-1 accuracy (all slices):  ON 100%   OFF 14%
★ HEADLINE — accuracy on ['adversarial_redherring', 'ambiguous']:
    probes ON : 100%
    probes OFF: 0%
    Δ (the whole thesis): +100%
```

If you see that: **you're set.** Screenshot it and drop into the JIRA ticket IC-6.

**Optional (only if you want to run the console visually):**
```bash
cd console && npm install && npm run dev
# In another terminal:
uvicorn server.app:app --port 8000
# Open http://localhost:5173
```

You don't need the console to author bundles. The harness is enough for the whole
authoring loop.

---

## 3. Running the harness — the daily loop

**Three commands you will run all day:**

```bash
# The graded ablation across all bundles — your fitness function
python -m ic.harness

# Single-incident deep dive — see every event, useful when debugging one bundle
python -m ic.investigate INC-4478 -o /tmp/pm.json

# Verify a sealed postmortem — sanity check
python verify.py /tmp/pm.json
```

**How to read `python -m ic.harness` output:**

```
INC-4478 [ambiguous] truth=H1/rb=True | ON=H1/True ✓ OFF=H2/False
```
- Left of `|` = your bundle's ground truth and rollback expectation.
- `ON=` = what the system decides *with* probes enabled. **Must match truth.**
- `OFF=` = what it decides *without* probes. **Should be wrong** (that's the point).
- `✓` = correct verdict. If ON has ✓ and OFF has no ✓, your bundle is good.

**If ON is wrong**, your bundle has a bug (probably §11).
**If OFF is also right**, you leaked the discriminator — see §4.

---

## 4. The invariant

**Read this section twice. Slowly.**

Every bundle has two evidence tiers:

- **`evidence.prior`** — the "cheap fan-out." The agents *always* see this. It is
  deliberately constructed so **more than one hypothesis is plausible from it alone**.
  This is the ambiguity.
- **`evidence.probeable`** — fetched **only** if the system decides to probe. **The
  single signal that discriminates between the competing hypotheses lives here, and
  nowhere in `prior`.**

**Why this matters:** the whole ablation (probes-on vs probes-off) is only meaningful
if turning probes off genuinely removes the discriminating signal. If the discriminator
leaks into `prior`, both ablation arms see it, and the +100% delta collapses to zero.
The whole thesis becomes untestable.

**The one authoring test you must always run mentally:**

> Cover the `evidence.probeable` block with your hand. Read only the prior. Can you
> already tell which hypothesis is right? If yes, you leaked. Move the giveaway
> signal into probeable.

This is the mistake beginners make most often. If your OFF arm scores right on your
new bundle, this is almost always why.

---

## 5. Reading an existing bundle end-to-end

Open [`corpus/INC-4471.json`](../corpus/INC-4471.json) and follow along.

- **`incident_id` / `slice` / `title`** — metadata. `slice` groups bundles for the
  harness report; use existing slice names (`ambiguous`, `adversarial_redherring`,
  `resource_exhaustion`, `cascading_dependency`, `deployment_induced`) unless
  we've decided to add a new slice type.
- **`ground_truth`** — the correct answer. Never shown to the agents; used only for
  grading. Includes `root_cause_id` (which of your hypotheses is right) and
  `rollback_correct` (should the system have recommended rolling back?).
- **`alert`** — the initial trigger. What the pager sees.
- **`topology`** — service dependency graph. The Telemetry Agent uses this to decide
  which neighbours to inspect.
- **`observables`** — controlled vocabulary of measurable things. Each hypothesis's
  `predicted_observations` references these by name. Keep names stable across bundles
  (`connection_wait_time`, `sibling_service_errors`, etc.) — the reasoner recognizes
  them.
- **`seed_hypotheses`** — the two (rarely three) competing theories. Each hypothesis
  declares:
  - `natural_owner`: which agent argues for it (`change_agent`, `telemetry_agent`, or
    `history_agent`). This is what makes them structurally disagree.
  - `predicted_observations`: what this hypothesis predicts a specific observable
    would show. **The disagreement between these predictions is what makes a probe
    discriminating.** If both hypotheses predict the same thing, no probe helps.
- **`evidence.prior`** — the always-visible clues. Mix of `metric`, `deployment`,
  `diff`, `prior_incident`, `runbook`. Both hypotheses must look plausible from
  reading only these.
- **`evidence.probeable`** — the deciding clue, hidden behind the probe. Only fetched
  if the system decides to probe.
- **`probe_catalog`** — the queries the system is *allowed* to run. Each has a
  `cost_ms` (used in the VoI scoring) and a `load_class`.

**Now read [`ic/reasoner.py`](../ic/reasoner.py) — specifically the three
`interpret_*` functions.** These are the three shapes of "the deciding question."

---

## 6. The three interpreter types

**This section is the whole engine, in three tables.** Every new bundle must fit one
of these three. If yours doesn't, either reshape it to fit or pick a different
postmortem.

### Type 1 — ORDERING ("did X rise before Y?")

**The question:** which signal moved first?
**The vocabulary:** predictions must be `rises_first` or `rises_later`.
**The interpreter:** `interpret_ordering` in `ic/reasoner.py`.
**The probe returns:** a time-series (list of `[timestamp, value]` pairs).
**Existing examples:** INC-4471 (connection-wait leads), INC-4474 (upstream leads),
INC-4476 (lock-wait leads).

**When to use:** cascading failures, resource contention that shows in a leading
indicator, anything where the causal chain is a race.

### Type 2 — SIBLING / THRESHOLD ("did the peer also fail?")

**The question:** did another service or component that shares the suspected cause
also break?
**The vocabulary:** predictions must be `stays_healthy` or `also_failing`.
**The interpreter:** `interpret_threshold` in `ic/reasoner.py`.
**The probe returns:** a time-series; the check is whether it crossed a fail
threshold (~2% error rate by default).
**Existing examples:** INC-4472 (cart-service check), INC-4475 (cart-service),
INC-4477 (billing-service).

**When to use:** deploy-vs-dependency questions, noisy-neighbor, shared-infra
faults. **The most common type. Reach for this one first when in doubt.**

### Type 3 — DISPERSION ("all-alike vs split by cohort?")

**The question:** does the failure look uniform across the fleet, or does it split
by pod age / shard / region?
**The vocabulary:** predictions must be `old_pods_slower` or `uniform_across_pods`.
**The interpreter:** `interpret_dispersion` in `ic/reasoner.py`.
**The probe returns:** a map of cohort → value; the check is the ratio of max to
min.
**Existing examples:** INC-4473 (pod age split).

**When to use:** memory leaks, connection leaks, capacity-vs-degradation, anything
where "is it everyone or a subset?" is the deciding question.

### Choosing between them — the decision tree

```
Is the deciding question "did X move BEFORE Y in time?"
  → ORDERING (Type 1)

Is it "does a peer service or component ALSO show this?"
  → SIBLING/THRESHOLD (Type 2)

Is it "does the failure SPLIT by some grouping (age/shard/region)?"
  → DISPERSION (Type 3)

None of the above?
  → The postmortem doesn't fit our current engine. Pick a different one
    OR file a JIRA ticket for a new interpreter (Phase 2 work; not for pre-final).
```

---

## 7. Authoring a bundle — the pipeline

The whole workflow is **7 steps, ~90 minutes per bundle** once you're warmed up.

### Step 1: Extract on a scratchpad (10 min)

For your chosen postmortem, write down in a plain text file:

```
POSTMORTEM: <url>
ALERT: <service> — <signal> — <time>
TRUE CAUSE (one sentence): <...>
PLAUSIBLE-BUT-WRONG THEORY (what responders considered first): <...>
DECIDING SIGNAL (the one thing that settled it): <...>
THE TRAP (what in the cheap evidence pointed the wrong way): <...>
```

If any of these are hard to fill in, **the postmortem is a bad candidate.** Move on.
You want the ones with an explicit *"we initially thought X but it turned out to be
Y"* moment.

### Step 2: Pick a template

Match your deciding signal to one of the three types in §6. Copy the closest
existing bundle from `corpus/` as your starting file:

- Ordering deciding signal → copy INC-4471 or INC-4474
- Sibling deciding signal → copy INC-4472 or INC-4475
- Dispersion deciding signal → copy INC-4473

**Rename it** to the next available `INC-####.json`.

### Step 3: LLM-assisted first draft (20 min)

See §8 for the exact prompt. Do not skip the human review that follows.

### Step 4: Human review — the invariant check (15 min)

Read your draft. Do the mental test from §4: cover `evidence.probeable`, read only
`prior`. Can you tell the answer? If yes, iterate.

Also check:
- [ ] Timestamps are internally consistent (cause precedes effect).
- [ ] Both hypotheses' `predicted_observations` disagree on at least one observable.
- [ ] That observable is measured by exactly one probe in `probe_catalog`.
- [ ] That probe's result is in `evidence.probeable`, not `prior`.
- [ ] `ground_truth.rollback_correct` is honestly set.
- [ ] `natural_owner` is different for each hypothesis (structural disagreement).

### Step 5: Harness test (2 min)

```bash
python -m ic.harness
```

Look for your new incident's row. **Success looks like:**

```
INC-####  [<slice>]  truth=H1/rb=True  |  ON=H1/True  ✓  OFF=H2/False
```

### Step 6: If either arm misbehaves — go to §9

### Step 7: Commit

```bash
git add corpus/INC-####.json
git commit -m "Author INC-####: <one-line description> (adapted from <postmortem name>)"
```

Include the postmortem URL in the commit message so future-you can trace it.

---

## 8. LLM-assisted workflow

You'll burn a whole day per bundle without this. With it, ~90 min.

**Rules of engagement:**
- The LLM writes the first draft.
- **You** run the invariant check (§4) — the LLM will confidently violate it.
- **You** run the harness — that's the ground truth.
- If the LLM's draft fails the harness twice, throw it out and hand-write.

**The exact prompt to paste into Claude / ChatGPT:**

```
I'm authoring a test-set incident bundle for an SRE decision-policy benchmark.
The corpus format is described here:

<PASTE contents of schema/incident_bundle.schema.md>

Here is a worked example I want you to match structurally:

<PASTE contents of corpus/INC-4471.json>

Here is the real postmortem I want to adapt:

<PASTE postmortem text>

Here is my scratchpad extraction:

POSTMORTEM: <url>
ALERT: <...>
TRUE CAUSE: <...>
PLAUSIBLE-BUT-WRONG THEORY: <...>
DECIDING SIGNAL: <...>
THE TRAP: <...>

Draft a bundle in the same JSON format. Requirements — these are load-bearing:

1. Both hypotheses must be genuinely plausible from evidence.prior alone.
2. The single discriminating signal MUST live only in evidence.probeable, NEVER
   in evidence.prior. Test yourself: can you tell the answer from prior alone?
   If yes, move that signal into probeable.
3. The two hypotheses must predict OPPOSING values for the discriminating
   observable — use the vocabulary "rises_first"/"rises_later" for ordering,
   "stays_healthy"/"also_failing" for sibling, or "old_pods_slower"/
   "uniform_across_pods" for dispersion.
4. Include one distractor probe with info_gain=0 (both hypotheses predict the
   same or neither predicts anything for its observable) so probe selection is
   non-trivial.
5. Timestamps must be internally coherent.
6. Return the JSON only, no prose commentary.
```

**Then run the harness. Don't trust; verify.**

---

## 9. Debug tree

Copy this section, keep it open while authoring.

### Probes OFF gets the right answer

**Diagnosis:** you leaked the discriminator into `prior`.
**Fix:** find the evidence item in `evidence.prior` whose data settles the case.
Move it to `evidence.probeable` and add a `probe_id` and a matching entry in
`probe_catalog`. Re-run harness.

### Probes ON gets the wrong answer

**Most common cause:** your hypotheses' `predicted_observations` don't use the
right vocabulary for their interpreter type.
- Ordering: `rises_first` / `rises_later`
- Sibling: `stays_healthy` / `also_failing`
- Dispersion: `old_pods_slower` / `uniform_across_pods`

**Second cause:** the observable in `predicted_observations` doesn't match the
observable in the probe's `measures` list.
**Third cause:** your probe's payload doesn't match the interpreter's expected shape
(time-series for ordering/sibling, map for dispersion).

### Both arms get the right answer

**Diagnosis:** the bundle is too easy. Prior alone is deciding it.
**Fix:** add a "trap" to prior — a signal that superficially supports the wrong
hypothesis. Look at INC-4471's `inventory-service/p99_latency` for a canonical
example (a benign blip that misleads).

### Both arms get it wrong

**Diagnosis:** the reasoner can't classify your winning hypothesis. Check the claim
text — the classifier looks for keywords (`deploy`, `pool`, `leak`, `upstream`,
`cascade`, `traffic`, etc.). Reword the claim to be closer to an existing bundle's
wording.

### Harness crashes

**Almost always:** JSON malformed. Run `python -c "import json;
json.load(open('corpus/INC-####.json'))"` to find the exact line.

### Harness runs but INC-#### doesn't appear

**Cause:** filename doesn't match `INC-*.json` glob. Check for typos.

---

## 10. Postmortem sources

**Highest signal (start here):**
- `github.com/danluu/post-mortems` — the canonical index, hundreds of them.
- `k8s.af` — Kubernetes-specific, well-structured writeups.
- Cloudflare blog → search "postmortem" or "incident report" (very high quality).
- GitHub Engineering blog → "incident report" writeups.
- AWS Post-Event Summaries → aws.amazon.com/premiumsupport/technology/pes/

**Academic:**
- **RCAEval** and **PetShop** papers' appendices describe scenarios you can adapt.

**How to spot a good candidate in 60 seconds:**
- ✅ The writeup contains the phrase "at first we thought" or "our initial
  hypothesis was" or "we suspected X but…"
- ✅ There's an explicit deciding moment where the responders checked one specific
  thing.
- ✅ The failure fits one of the three types in §6.
- ❌ Skip: "we had a bad day, here's the chronology" without a competing-hypothesis
  moment.
- ❌ Skip: incidents where the discriminator was a novel custom check we don't
  have an interpreter for.

**Realistic yield:** roughly 1 in 4 postmortems you skim will make a good
candidate. Don't force ones that don't fit.

---

## 11. Common authoring mistakes (the "gotchas")

### 1. Leaking the discriminator into prior

Covered in §4. The #1 mistake. Always mentally test by covering `probeable`.

### 2. Using the wrong prediction vocabulary

The reasoner dispatches by keyword. `"rises_first"` works; `"rises early"` does
not. Case-sensitive. Copy from an existing bundle.

### 3. Making the two hypotheses non-opposing

Both hypotheses predicting `"rises_first"` for the same observable means the probe
returns the same result either way — its info-gain is zero. Predictions **must
disagree** on at least one observable.

### 4. `natural_owner` mismatch

If both hypotheses have `natural_owner: change_agent`, the same agent argues both
sides — they can't disagree. Use `change_agent` vs `telemetry_agent` for the
canonical disagreement, or `telemetry_agent` vs `change_agent` for cascades.

### 5. Deploy-keyword collision

The rollback classifier keys on `"deploy"`, `"pool"`, `"config"`, `"rollback"`. If
you use "deploy" in your non-deploy hypothesis (e.g., *"regardless of the recent
deploy…"*), the negation cues (`"regardless"`, `"unrelated"`, `"despite"`) will
save you — but only if you include them. See INC-4472 for the pattern.

### 6. Author notes leaking into agent context

Any key starting with `_` in the evidence items (e.g., `_note_for_authors`) is
stripped before agents see it. Use those liberally for your own notes — they don't
break anything.

### 7. Time zones

All timestamps are UTC with `Z` suffix. Don't use `+00:00` or naive strings.

### 8. Slice names

Pre-final scope uses only: `ambiguous`, `adversarial_redherring`,
`resource_exhaustion`, `cascading_dependency`, `deployment_induced`, `novel`. New
slice types need a JIRA discussion — don't invent one.

---

## 12. What NOT to change

**Never touch during the pre-final sprint:**
- Anything in `ic/` (engine code)
- `schema/incident_bundle.schema.md` (unless we've agreed to add the
  `intervention_result` tier as part of the Sprint 2 build)
- Existing bundles (adding is fine; editing existing ones can break the harness
  headline and reproducibility)

**If your bundle needs one of these to work, the bundle is wrong — not the code.**
Pick a different postmortem that fits an existing interpreter type.

**Exception:** if you keep hitting the same "no interpreter fits" wall across
multiple postmortems, that's a signal — file a JIRA ticket describing the new
interpreter type. That's Phase 2 work, but it's real feedback and we want it.

---

## Quick reference card (pin this)

```
Type 1 ORDERING     predictions: rises_first / rises_later
                    probe payload: {"series": [[ts, val], ...]}
                    example: INC-4471

Type 2 SIBLING      predictions: stays_healthy / also_failing
                    probe payload: {"series": [[ts, val], ...]}
                    example: INC-4472

Type 3 DISPERSION   predictions: old_pods_slower / uniform_across_pods
                    probe payload: {"map": {"cohort_a": val, ...}}
                    example: INC-4473

Daily commands:
  python -m ic.harness                          # graded ablation
  python -m ic.investigate INC-#### -o /tmp/p   # single incident deep dive
  python verify.py /tmp/p                       # verify a seal

Golden test (always do this before commit):
  Cover evidence.probeable. Can you tell the answer from prior?
  If yes → you leaked.
```

---

## Getting unstuck

- **Something conceptual isn't clicking:** ping Shahy. Don't grind on it alone for
  more than 30 minutes.
- **The harness is failing and §9 didn't help:** paste your bundle JSON + the
  harness output into a JIRA comment and ping Shahy.
- **A postmortem seems close but doesn't quite fit:** write a one-paragraph
  candidate summary in a JIRA sub-ticket. We'll triage together before you spend
  authoring time on it.
- **You want to try a fourth interpreter type:** great instinct. File a Phase 2
  ticket describing the type and one example. Do not build it now.

Welcome to the team. This is the highest-leverage work on the project right now.
