"""
test_json_contract.py — run this FIRST, before trusting the live agent loop.

Your three agents live or die on returning valid JSON matching a Pydantic schema.
Free open-weight models are inconsistent at that. This script fires the same
structured request N times at whatever IC_PROVIDER is set to, and reports how many
came back clean. If the hit rate is low, you know BEFORE it silently corrupts your
ablation numbers (a broken JSON contract looks like a reasoning failure but isn't).

Usage:
    # in .env set IC_USE_LLM=1 and IC_PROVIDER=openrouter_free (or _pinned, or anthropic)
    python test_json_contract.py
    python test_json_contract.py --n 20        # more samples

Interpreting results:
    10/10  -> build on it freely.
    7-9/10 -> the retry+failover in ic/llm.py will paper over it for iteration,
              but DO NOT run your calibration harness on this model — pin a
              stronger one (IC_PROVIDER=openrouter_pinned or anthropic) for numbers.
    <7/10  -> don't build the loop on this model. Switch provider.

With no real key in .env this prints 0/N and a clear "missing key" message — that is
the expected, graceful failure, not a crash.
"""

from __future__ import annotations

import argparse
import os

from pydantic import BaseModel, Field

from ic.llm import active_chain, json_call


# A miniature stand-in for a real agent's output — same SHAPE your Hypothesis uses:
# a claim, a bounded confidence, and a list of cited evidence ids.
class HypothesisProbe(BaseModel):
    root_cause: str = Field(..., description="one-sentence most likely cause")
    confidence: float = Field(..., ge=0.0, le=1.0)
    evidence_ids: list[str] = Field(..., description="ids of evidence supporting the claim")


SYSTEM = (
    "You are an SRE incident triage agent. Given a short incident description, output "
    "your single most likely root cause, a calibrated confidence between 0 and 1, and "
    "the evidence ids you relied on."
)

USER = (
    "Incident: checkout-service 5xx rate jumped to 12% three minutes after deploy "
    "v2.14.0, which reduced the DB connection pool from 50 to 10. DB latency and CPU "
    "both spiked. Evidence available: ev_deploy, ev_diff, ev_cpu, ev_dblatency.\n"
    "Return your hypothesis."
)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=10, help="number of samples")
    args = ap.parse_args()

    provider = os.environ.get("IC_PROVIDER", "openrouter_free")
    chain = " -> ".join(p.name + "(" + p.model + ")" for p in active_chain())
    print(f"IC_PROVIDER={provider}")
    print(f"failover chain: {chain}\n")

    ok = 0
    for i in range(1, args.n + 1):
        try:
            h = json_call(SYSTEM, USER, HypothesisProbe)
            ok += 1
            print(f"  {i:>2}. OK   conf={h.confidence:.2f}  cause={h.root_cause[:60]!r}")
        except Exception as e:
            print(f"  {i:>2}. FAIL {str(e).splitlines()[0][:80]}")

    rate = ok / args.n
    print(f"\nJSON contract: {ok}/{args.n} clean ({rate:.0%})")
    if rate >= 1.0:
        print("Verdict: solid. Build the agent loop on this provider.")
    elif rate >= 0.7:
        print("Verdict: usable for ITERATION (retry/failover covers the gaps), "
              "but pin a stronger model for the harness/calibration run.")
    else:
        print("Verdict: too flaky (or no key). Set a real key / switch IC_PROVIDER "
              "before building the loop.")


if __name__ == "__main__":
    main()
