"""Incident Commander — multi-agent active differential diagnosis for SRE incidents."""

__version__ = "0.1.0"

# Single swappable model constant (see README). Used only when IC_USE_LLM=1 and a
# key is present; the deterministic analytical engine is the offline default.
MODEL = "claude-sonnet-5"
