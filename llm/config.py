"""Model selection and pricing configuration for the overnight PCB-engine
LLM operations layer.

All model ids are OpenRouter ids. Mid-tier (Llama-70B class) models must be
able to drive the design/review loops; the frontier model is reserved for
escalation. Prices were verified against a live GET
https://openrouter.ai/api/v1/models on 2026-06-10.
"""

from __future__ import annotations

import os

OPENROUTER_BASE = "https://openrouter.ai/api/v1"

# llama-3.3-70b-instruct supersedes 3.1-70b on OpenRouter (verified live:
# both are listed, 3.3 is newer and cheaper: $0.10/$0.32 vs $0.40/$0.40 per Mtok).
DESIGN_MODEL = os.environ.get("DESIGN_MODEL", "meta-llama/llama-3.3-70b-instruct")
REVIEW_MODEL = os.environ.get("REVIEW_MODEL", "meta-llama/llama-3.3-70b-instruct")
FRONTIER_MODEL = os.environ.get("FRONTIER_MODEL", "anthropic/claude-sonnet-4.5")

# (input_usd_per_mtok, output_usd_per_mtok)
# Populated from the live /models response on 2026-06-10:
#   meta-llama/llama-3.3-70b-instruct: prompt 0.0000001, completion 0.00000032 ($/tok)
#   anthropic/claude-sonnet-4.5:       prompt 0.000003,  completion 0.000015   ($/tok)
PRICE_TABLE: dict[str, tuple[float, float]] = {
    "meta-llama/llama-3.3-70b-instruct": (0.10, 0.32),
    "google/gemini-2.5-flash": (0.30, 2.50),
    "google/gemini-2.5-pro": (1.25, 10.0),
    "qwen/qwen3-235b-a22b": (0.20, 1.20),
    "openai/gpt-4.1-mini": (0.40, 1.60),
    "anthropic/claude-sonnet-4.5": (3.0, 15.0),
}

# Conservative default for models not in the table — assume mid-tier-plus
# pricing so the spend tracker over-estimates rather than under-estimates.
_DEFAULT_PRICE: tuple[float, float] = (1.0, 3.0)


def price_for(model: str) -> tuple[float, float]:
    """Return (input_usd_per_mtok, output_usd_per_mtok) for a model id."""
    return PRICE_TABLE.get(model, _DEFAULT_PRICE)


MAX_TOTAL_SPEND_USD = float(os.environ.get("MAX_TOTAL_SPEND_USD", "10.0"))
