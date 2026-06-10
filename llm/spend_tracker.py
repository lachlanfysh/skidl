"""Run-level LLM spend tracking with a hard budget cap.

A single SpendTracker is shared across all calls in a corpus run. preflight()
is called by the client BEFORE each request with a conservative cost estimate;
commit() records actual cost after success and appends a JSON line to the
spend log.
"""

from __future__ import annotations

import json
import os
import threading
import time
from typing import Optional

from llm.config import price_for
from llm.openrouter_client import BudgetExhausted, LLMResponse


def _atomic_append(path: str, line: str) -> None:
    """Append one line with O_APPEND semantics (atomic for short writes).

    telemetry/store.py did not exist when this module was written (it is being
    built in parallel); if telemetry.store.atomic_append(path, line) appears
    later, this helper can be swapped for it.
    """
    data = (line + "\n").encode("utf-8")
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
    try:
        os.write(fd, data)
    finally:
        os.close(fd)


class SpendTracker:
    """Thread-safe cumulative spend tracker with a hard cap."""

    def __init__(self, cap_usd: float, log_path: Optional[str] = None):
        self.cap_usd = float(cap_usd)
        self.log_path = log_path
        self._lock = threading.Lock()
        self._spent_usd = 0.0

    @property
    def spent_usd(self) -> float:
        with self._lock:
            return self._spent_usd

    @property
    def exhausted(self) -> bool:
        with self._lock:
            return self._spent_usd >= self.cap_usd

    def preflight(self, messages: list[dict], max_tokens: int, model: str) -> None:
        """Raise BudgetExhausted if this call could push spend over the cap.

        Estimate: chars/4 as input tokens, full max_tokens as output tokens —
        deliberately conservative on the output side.
        """
        in_price, out_price = price_for(model)
        total_chars = sum(len(str(m.get("content") or "")) for m in messages)
        estimate = (total_chars / 4 / 1e6 * in_price) + (max_tokens / 1e6 * out_price)
        with self._lock:
            if self._spent_usd + estimate > self.cap_usd:
                raise BudgetExhausted(
                    f"spend cap ${self.cap_usd:.2f} would be exceeded: "
                    f"spent ${self._spent_usd:.4f} + estimated ${estimate:.4f} ({model})"
                )

    def commit(self, resp: LLMResponse) -> None:
        """Record the actual cost of a completed call and log it."""
        with self._lock:
            self._spent_usd += resp.cost_usd
            cumulative = self._spent_usd
        if self.log_path:
            line = json.dumps(
                {
                    "ts": time.time(),
                    "model": resp.model,
                    "cost_usd": resp.cost_usd,
                    "cumulative_usd": cumulative,
                },
                separators=(",", ":"),
            )
            _atomic_append(self.log_path, line)
