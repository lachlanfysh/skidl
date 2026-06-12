"""Thin async OpenRouter chat-completions client.

One public coroutine — ``complete()`` — with retry/backoff, defensive usage
parsing, table-based cost fallback, and optional budget enforcement through a
SpendTracker. An httpx.AsyncClient can be injected for testing (all tests
mock HTTP via httpx.MockTransport; no key is required at test time).
"""

from __future__ import annotations

import asyncio
import os
import time
from typing import Any, Optional

import httpx
from pydantic import BaseModel

from llm.config import OPENROUTER_BASE, price_for


class LLMResponse(BaseModel):
    """Normalized result of one completion call."""

    text: str
    model: str
    tokens_in: int
    tokens_out: int
    latency_s: float
    cost_usd: float
    cost_source: str  # "api" (OpenRouter-reported credits cost) | "table" (PRICE_TABLE estimate)


class LLMUnavailable(Exception):
    """The model could not be reached or refused the request (after retries)."""


class BudgetExhausted(Exception):
    """The spend tracker refused the call — cumulative cost would exceed the cap."""


# Seconds slept before retry attempts 2 and 3 (and any further, if raised).
# Module-level so tests can monkeypatch it to zeros.
RETRY_BACKOFF_S: tuple[float, ...] = (1.0, 4.0, 10.0)
MAX_ATTEMPTS = 3


def _parse_response(data: dict, model: str, latency_s: float) -> LLMResponse:
    try:
        text = data["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError) as err:
        raise LLMUnavailable(f"malformed OpenRouter response: {err!r}") from err

    usage = data.get("usage") or {}
    tokens_in = int(usage.get("prompt_tokens") or 0)
    tokens_out = int(usage.get("completion_tokens") or 0)

    # With "usage": {"include": true} OpenRouter reports the credits cost in
    # usage.cost — read it defensively, fall back to the price table.
    cost = usage.get("cost")
    if isinstance(cost, (int, float)) and not isinstance(cost, bool):
        cost_usd = float(cost)
        cost_source = "api"
    else:
        in_price, out_price = price_for(model)
        cost_usd = tokens_in / 1e6 * in_price + tokens_out / 1e6 * out_price
        cost_source = "table"

    return LLMResponse(
        text=text,
        model=str(data.get("model") or model),
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        latency_s=latency_s,
        cost_usd=cost_usd,
        cost_source=cost_source,
    )


async def complete(
    messages: list[dict],
    model: str,
    max_tokens: int = 16384,
    temperature: float = 0.2,
    response_format: Optional[dict] = None,
    web_search: bool = False,
    tools: Optional[list[dict]] = None,
    spend_tracker: Any = None,
    client: Optional[httpx.AsyncClient] = None,
) -> LLMResponse:
    """POST one chat completion to OpenRouter and return a normalized response.

    Raises LLMUnavailable (no API key, non-retryable error, or retries
    exhausted) or BudgetExhausted (propagated from spend_tracker.preflight).
    """
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        raise LLMUnavailable("OPENROUTER_API_KEY is not set")

    if spend_tracker is not None:
        spend_tracker.preflight(messages, max_tokens, model)

    body: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "usage": {"include": True},
    }
    if response_format is not None:
        body["response_format"] = response_format
    if web_search:
        body.setdefault("tools", []).append({
            "type": "openrouter:web_search",
            "parameters": {"max_results": 5, "search_context_size": "medium"},
        })
    if tools:
        body.setdefault("tools", []).extend(tools)

    headers = {"Authorization": f"Bearer {api_key}"}
    url = f"{OPENROUTER_BASE}/chat/completions"

    owns_client = client is None
    if owns_client:
        client = httpx.AsyncClient(timeout=httpx.Timeout(180.0))

    try:
        last_error = "no attempt made"
        for attempt in range(MAX_ATTEMPTS):
            if attempt > 0:
                await asyncio.sleep(RETRY_BACKOFF_S[min(attempt - 1, len(RETRY_BACKOFF_S) - 1)])
            t0 = time.monotonic()
            try:
                http_resp = await client.post(url, headers=headers, json=body)
            except httpx.TransportError as err:
                last_error = f"transport error: {err!r}"
                continue
            latency_s = time.monotonic() - t0

            if http_resp.status_code == 429 or http_resp.status_code >= 500:
                last_error = f"HTTP {http_resp.status_code}: {http_resp.text[:500]}"
                continue
            if http_resp.status_code >= 400:
                # Non-retryable client error (bad key, bad request, ...).
                raise LLMUnavailable(
                    f"OpenRouter rejected request (HTTP {http_resp.status_code}): "
                    f"{http_resp.text[:500]}"
                )

            try:
                data = http_resp.json()
            except ValueError as err:
                last_error = f"non-JSON 200 response: {err!r}"
                continue

            resp = _parse_response(data, model, latency_s)
            if spend_tracker is not None:
                spend_tracker.commit(resp)
            return resp

        raise LLMUnavailable(
            f"OpenRouter unavailable after {MAX_ATTEMPTS} attempts ({model}): {last_error}"
        )
    finally:
        if owns_client:
            await client.aclose()
