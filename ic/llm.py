"""
ic/llm.py — the single choke point for every LLM call in Incident Commander.

Design goals:
  * ONE call shape for the whole system: `json_call(system, user, schema_model)`.
    Every agent returns strict JSON validated against a Pydantic model.
  * ONE place to swap providers. Iterate for free on OpenRouter, run the harness on a
    pinned free model for reproducible numbers, do the final quality pass on Claude —
    without touching any agent code.
  * Provider swap is just base_url + model + key. We use the Anthropic Messages format
    throughout, because OpenRouter exposes an Anthropic-compatible /messages endpoint,
    so the same client works for both OpenRouter-free and Claude direct.
  * JSON-contract failover: if a (weak, free) model returns unparseable or
    schema-invalid JSON, retry once, then fail over to the next provider in the chain.
    This keeps a flaky free model from silently corrupting your ablation numbers.

You never pass a key to anyone. Keys are read from the local environment (.env).

NOTE ON base_url: the Anthropic SDK appends "/v1/messages" to base_url, so for
OpenRouter the base must be "https://openrouter.ai/api" (→ /api/v1/messages).
Using ".../api/v1" would double the prefix to ".../api/v1/v1/messages" (404). This was
verified against anthropic SDK 0.116 `_prepare_url`.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Type, TypeVar

from pydantic import BaseModel, ValidationError

try:
    from dotenv import load_dotenv
    load_dotenv()  # pulls OPENROUTER_API_KEY / ANTHROPIC_API_KEY / IC_* from .env
except Exception:  # python-dotenv optional; env may already be populated
    pass

T = TypeVar("T", bound=BaseModel)

# base ends at /api on purpose — the SDK adds /v1/messages (see module docstring).
OPENROUTER_BASE_URL = "https://openrouter.ai/api"


# --------------------------------------------------------------------------- #
# Provider configuration
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Provider:
    name: str
    model: str
    base_url: str | None      # None => Anthropic's own default endpoint
    api_key_env: str

    def has_key(self) -> bool:
        key = os.environ.get(self.api_key_env)
        return bool(key) and not key.endswith("REPLACE_ME")

    def client(self):
        from anthropic import Anthropic  # lazy: SDK not needed for the offline engine
        key = os.environ.get(self.api_key_env)
        if not key or key.endswith("REPLACE_ME"):
            raise RuntimeError(
                f"[{self.name}] missing/placeholder key in env var {self.api_key_env}. "
                f"Put your real key in .env (see .env.example)."
            )
        kwargs = {"api_key": key}
        if self.base_url:
            kwargs["base_url"] = self.base_url
        return Anthropic(**kwargs)


def _pinned_slug() -> str:
    return os.environ.get("IC_PINNED_MODEL", "openai/gpt-oss-120b:free")


def _anthropic_model() -> str:
    return os.environ.get("IC_ANTHROPIC_MODEL", "claude-sonnet-5")


def _provider(name: str) -> Provider:
    if name == "openrouter_free":
        # Resilient auto-router. Use WHILE TUNING PROMPTS. Not for calibration numbers
        # (it picks a random model per call, so Brier/accuracy would be a grab-bag).
        return Provider("openrouter_free", "openrouter/free", OPENROUTER_BASE_URL, "OPENROUTER_API_KEY")
    if name == "openrouter_pinned":
        # A single fixed free slug. Use FOR THE HARNESS so every incident is scored by
        # the same model and the numbers mean something. Verify it's still ":free".
        return Provider("openrouter_pinned", _pinned_slug(), OPENROUTER_BASE_URL, "OPENROUTER_API_KEY")
    if name == "anthropic":
        # Claude direct. Use for the FINAL quality pass — confirm the agent-disagreement
        # and probe mechanism survive on a strong model before you stake the demo on it.
        return Provider("anthropic", _anthropic_model(), None, "ANTHROPIC_API_KEY")
    raise ValueError(f"Unknown provider: {name!r}")


# Failover order per active provider. If the primary breaks the JSON contract, we walk
# down the chain rather than crashing mid-investigation.
_FAILOVER = {
    "openrouter_free":   ["openrouter_free", "openrouter_pinned", "anthropic"],
    "openrouter_pinned": ["openrouter_pinned", "openrouter_free", "anthropic"],
    "anthropic":         ["anthropic"],  # no silent fallback off Claude during a graded run
}


def active_chain() -> list[Provider]:
    primary = os.environ.get("IC_PROVIDER", "openrouter_free")
    return [_provider(n) for n in _FAILOVER.get(primary, [primary])]


# --------------------------------------------------------------------------- #
# Availability gate — lets the offline engine stay the default
# --------------------------------------------------------------------------- #
def llm_enabled() -> bool:
    """Master switch. Agents use the model only when IC_USE_LLM=1 AND at least one
    provider in the active chain has a real key. Otherwise the deterministic analytical
    engine runs — so the repo works fully offline with no key."""
    if os.environ.get("IC_USE_LLM") != "1":
        return False
    return any(p.has_key() for p in active_chain())


# --------------------------------------------------------------------------- #
# The one call everything uses
# --------------------------------------------------------------------------- #
def _extract_text(resp) -> str:
    """Concatenate text blocks from an Anthropic-format response."""
    parts = []
    for block in resp.content:
        text = getattr(block, "text", None)
        if text:
            parts.append(text)
    return "\n".join(parts).strip()


def _strip_fences(s: str) -> str:
    """Weak models love to wrap JSON in ```json fences. Strip them."""
    s = s.strip()
    if s.startswith("```"):
        s = s.split("```", 2)[1] if s.count("```") >= 2 else s.lstrip("`")
        if s.lstrip().startswith("json"):
            s = s.lstrip()[4:]
    return s.strip().strip("`").strip()


def _parse_into(schema: Type[T], raw: str) -> T:
    """Parse model text into the Pydantic schema, or raise."""
    data = json.loads(_strip_fences(raw))
    return schema.model_validate(data)


def json_call(
    system: str,
    user: str,
    schema: Type[T],
    *,
    max_tokens: int = 1500,
    temperature: float = 0.2,
) -> T:
    """
    Call the active provider chain and return an instance of `schema`.

    - Appends a hard instruction to emit ONLY JSON matching the schema.
    - Retries once on the same provider (parse/validation failure).
    - Fails over to the next provider in the chain if the retry also fails.
    - Raises RuntimeError only if EVERY provider fails the contract.
    """
    schema_hint = (
        "Respond with a SINGLE JSON object and nothing else — no prose, no markdown, "
        "no code fences. It must validate against this JSON schema:\n"
        f"{json.dumps(schema.model_json_schema(), separators=(',', ':'))}"
    )
    full_system = f"{system}\n\n{schema_hint}"

    errors: list[str] = []
    for provider in active_chain():
        try:
            client = provider.client()
        except RuntimeError as e:
            errors.append(str(e))
            continue  # key missing for this provider; try the next

        for attempt in (1, 2):  # one retry on the same provider
            try:
                resp = client.messages.create(
                    model=provider.model,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    system=full_system,
                    messages=[{"role": "user", "content": user}],
                )
                raw = _extract_text(resp)
                return _parse_into(schema, raw)
            except (json.JSONDecodeError, ValidationError) as e:
                errors.append(f"[{provider.name} attempt {attempt}] bad JSON: {e}")
                continue
            except Exception as e:  # network / rate limit / auth — move to next provider
                errors.append(f"[{provider.name} attempt {attempt}] call failed: {e}")
                break

    raise RuntimeError(
        "json_call: every provider failed the JSON contract.\n  " + "\n  ".join(errors)
    )


# --------------------------------------------------------------------------- #
# Prompt helper — evidence enters as delimited, typed, hashed data, never as an
# instruction (prompt-injection containment; see README guardrails).
# --------------------------------------------------------------------------- #
def evidence_block(items) -> str:
    lines = []
    for e in items:
        lines.append(
            f"<evidence source_uri={e.source_uri!r} type={e.source_type!r} "
            f"hash={e.content_hash()[:12]} measures={e.measures}>\n"
            f"{json.dumps(e.payload, separators=(',', ':'))}\n</evidence>"
        )
    return "\n".join(lines)
