"""Tests for the OpenRouter LLM operations layer.

All HTTP is mocked via httpx.MockTransport — OPENROUTER_API_KEY is never
required (a fake key is injected per-test) and no network traffic occurs.
"""

import json

import httpx
import pytest

import llm.openrouter_client as orc
from llm.config import price_for
from llm.openrouter_client import BudgetExhausted, LLMUnavailable, complete
from llm.operations import (
    SpecParseError,
    external_agent_review,
    nl_to_input_spec,
    review_exceptions,
)
from llm.spend_tracker import SpendTracker
from schemas.circuit_spec import CircuitSpec

MODEL = "meta-llama/llama-3.3-70b-instruct"


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def or_response(text, prompt_tokens=100, completion_tokens=50, cost=None, model=MODEL):
    """Build an OpenRouter-shaped chat completion body."""
    usage = {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens}
    if cost is not None:
        usage["cost"] = cost
    return {
        "id": "gen-123",
        "model": model,
        "choices": [{"message": {"role": "assistant", "content": text}}],
        "usage": usage,
    }


def make_client(bodies, status_codes=None, calls=None):
    """AsyncClient over a MockTransport that replays bodies in sequence.

    bodies: list of dicts (response JSON). status_codes: matching list of
    ints (default all 200). calls: optional list collecting parsed request
    payloads for assertions.
    """
    state = {"i": 0}
    status_codes = status_codes or [200] * len(bodies)

    def handler(request):
        i = min(state["i"], len(bodies) - 1)
        state["i"] += 1
        if calls is not None:
            calls.append(json.loads(request.content.decode()))
        return httpx.Response(status_codes[i], json=bodies[i])

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


VALID_SPEC = {
    "board": {"name": "i2c-breakout"},
    "parts": [
        {
            "ref": "U1",
            "lib": None,
            "footprint": "Package_TO_SOT_SMD:SOT-23-6",
            "pins": [
                {"num": "1", "name": "VCC", "func": "power_in"},
                {"num": "2", "name": "GND", "func": "power_in"},
                {"num": "3", "name": "SDA", "func": "bidirectional"},
                {"num": "4", "name": "SCL", "func": "input"},
            ],
        },
        {
            "ref": "C1",
            "lib": "Device",
            "part": "C",
            "value": "100nF",
            "footprint": "Capacitor_SMD:C_0603_1608Metric",
        },
    ],
    "nets": [
        {"name": "VCC", "power": True, "pins": ["U1.VCC", "C1.1"]},
        {"name": "GND", "power": True, "pins": ["U1.GND", "C1.2"]},
    ],
}

EXCEPTIONS = [
    {
        "id": "e1",
        "code": "SPEC_UNKNOWN_PIN",
        "severity": "fatal",
        "message": "U1 has no pin named 'VDDIO'",
        "candidates": [
            {"id": "c1", "action": "replace_pin", "human_summary": "Rename U1.VDDIO to U1.VDD"},
            {"id": "c2", "action": "remove_net_pin", "human_summary": "Drop U1.VDDIO from net VCC"},
        ],
    },
    {
        "id": "e2",
        "code": "LAYOUT_OUTLINE_VIOLATION",
        "severity": "error",
        "message": "J1 extends 3mm past the board outline",
        "candidates": [
            {"id": "c1", "action": "scale_outline", "human_summary": "Grow board area by 1.2x"},
            {"id": "c2", "action": "set_outline", "human_summary": "Set outline to 60x45mm"},
        ],
    },
]


@pytest.fixture(autouse=True)
def fake_api_key(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test-not-real")
    monkeypatch.setattr(orc, "RETRY_BACKOFF_S", (0.0, 0.0, 0.0))


# --------------------------------------------------------------------------
# 1. complete() — token/cost parsing
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_complete_parses_tokens_and_api_cost():
    calls = []
    async with make_client(
        [or_response("hello", prompt_tokens=120, completion_tokens=34, cost=0.000123)],
        calls=calls,
    ) as client:
        resp = await complete(
            [{"role": "user", "content": "hi"}], MODEL, client=client
        )
    assert resp.text == "hello"
    assert resp.model == MODEL
    assert resp.tokens_in == 120
    assert resp.tokens_out == 34
    assert resp.cost_usd == pytest.approx(0.000123)
    assert resp.cost_source == "api"
    assert resp.latency_s >= 0.0
    # Request body carries usage accounting and auth header conventions.
    assert calls[0]["usage"] == {"include": True}
    assert calls[0]["model"] == MODEL


@pytest.mark.asyncio
async def test_complete_falls_back_to_price_table():
    async with make_client(
        [or_response("hello", prompt_tokens=1000, completion_tokens=500)]
    ) as client:
        resp = await complete(
            [{"role": "user", "content": "hi"}], MODEL, client=client
        )
    in_price, out_price = price_for(MODEL)
    expected = 1000 / 1e6 * in_price + 500 / 1e6 * out_price
    assert resp.cost_usd == pytest.approx(expected)
    assert resp.cost_source == "table"


@pytest.mark.asyncio
async def test_complete_requires_api_key(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    with pytest.raises(LLMUnavailable, match="OPENROUTER_API_KEY"):
        await complete([{"role": "user", "content": "hi"}], MODEL)


# --------------------------------------------------------------------------
# 2. complete() — retry behavior
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_complete_retries_on_429_then_succeeds():
    bodies = [{"error": "rate limited"}, or_response("recovered")]
    calls = []
    async with make_client(bodies, status_codes=[429, 200], calls=calls) as client:
        resp = await complete(
            [{"role": "user", "content": "hi"}], MODEL, client=client
        )
    assert resp.text == "recovered"
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_complete_exhausts_retries_on_5xx():
    bodies = [{"error": "boom"}] * 3
    calls = []
    async with make_client(bodies, status_codes=[500, 502, 503], calls=calls) as client:
        with pytest.raises(LLMUnavailable, match="after 3 attempts"):
            await complete([{"role": "user", "content": "hi"}], MODEL, client=client)
    assert len(calls) == 3


# --------------------------------------------------------------------------
# 3. SpendTracker — budget enforcement
# --------------------------------------------------------------------------

def test_spend_tracker_blocks_at_zero_cap():
    tracker = SpendTracker(cap_usd=0.0)
    with pytest.raises(BudgetExhausted):
        tracker.preflight([{"role": "user", "content": "hello"}], 1024, MODEL)


@pytest.mark.asyncio
async def test_complete_preflights_before_any_http():
    def handler(request):  # pragma: no cover - must never run
        raise AssertionError("HTTP request made despite exhausted budget")

    tracker = SpendTracker(cap_usd=0.0)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(BudgetExhausted):
            await complete(
                [{"role": "user", "content": "hi"}],
                MODEL,
                spend_tracker=tracker,
                client=client,
            )


@pytest.mark.asyncio
async def test_spend_tracker_commits_and_logs(tmp_path):
    log = tmp_path / "spend.jsonl"
    tracker = SpendTracker(cap_usd=5.0, log_path=str(log))
    async with make_client([or_response("ok", cost=0.01)]) as client:
        await complete(
            [{"role": "user", "content": "hi"}], MODEL,
            spend_tracker=tracker, client=client,
        )
    assert tracker.spent_usd == pytest.approx(0.01)
    assert not tracker.exhausted
    entry = json.loads(log.read_text().strip())
    assert entry["model"] == MODEL
    assert entry["cost_usd"] == pytest.approx(0.01)
    assert entry["cumulative_usd"] == pytest.approx(0.01)
    assert "ts" in entry


# --------------------------------------------------------------------------
# 4. nl_to_input_spec
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_nl_to_spec_valid_first_try():
    # Fenced output exercises the defensive fence stripping.
    text = "```json\n" + json.dumps(VALID_SPEC) + "\n```"
    async with make_client([or_response(text)]) as client:
        spec, stages = await nl_to_input_spec(
            "A tiny I2C sensor breakout.", "i2c-breakout", client=client
        )
    assert isinstance(spec, CircuitSpec)
    assert spec.board.name == "i2c-breakout"
    assert {p.ref for p in spec.parts} == {"U1", "C1"}
    assert len(stages) == 1
    assert stages[0]["stage"] == "design_nl_to_input"
    assert stages[0]["model"] == MODEL
    assert stages[0]["tokens_in"] == 100


@pytest.mark.asyncio
async def test_nl_to_spec_repairs_invalid_json():
    bodies = [
        or_response("Sure! Here's a spec: {definitely not json"),
        or_response(json.dumps(VALID_SPEC)),
    ]
    calls = []
    async with make_client(bodies, calls=calls) as client:
        spec, stages = await nl_to_input_spec(
            "A tiny I2C sensor breakout.", "i2c-breakout", client=client
        )
    assert isinstance(spec, CircuitSpec)
    assert len(stages) == 2
    assert all(s["stage"] == "design_nl_to_input" for s in stages)
    # Repair call must carry the failed output and the correction instruction.
    repair_msgs = calls[1]["messages"]
    assert repair_msgs[-2]["role"] == "assistant"
    assert "corrected complete JSON object only" in repair_msgs[-1]["content"]


@pytest.mark.asyncio
async def test_nl_to_spec_raises_after_failed_repair():
    bad_spec = {"board": {"name": "x"}, "parts": [], "nets": "not-a-list"}
    bodies = [or_response("garbage"), or_response(json.dumps(bad_spec))]
    async with make_client(bodies) as client:
        with pytest.raises(SpecParseError) as excinfo:
            await nl_to_input_spec("desc", "board-x", client=client)
    assert len(excinfo.value.stages) == 2


# --------------------------------------------------------------------------
# 5. review_exceptions / external_agent_review
# --------------------------------------------------------------------------

def choices_json(pairs):
    return json.dumps(
        [{"exception_id": e, "candidate_id": c} for e, c in pairs]
    )


@pytest.mark.asyncio
async def test_review_valid_first_try():
    bodies = [or_response(choices_json([("e1", "c2"), ("e2", "c1")]))]
    async with make_client(bodies) as client:
        choices, stages, fallback_used = await review_exceptions(
            EXCEPTIONS, {"board": "feather-clone"}, [], client=client
        )
    assert fallback_used is False
    assert len(stages) == 1
    assert stages[0]["stage"] == "review_internal"
    assert {c["exception_id"]: c["candidate_id"] for c in choices} == {
        "e1": "c2",
        "e2": "c1",
    }


@pytest.mark.asyncio
async def test_review_invalid_candidate_then_valid_on_retry():
    bodies = [
        or_response(choices_json([("e1", "c9"), ("e2", "c1")])),  # c9 invalid
        or_response(choices_json([("e1", "c1"), ("e2", "c2")])),
    ]
    calls = []
    async with make_client(bodies, calls=calls) as client:
        choices, stages, fallback_used = await review_exceptions(
            EXCEPTIONS, {}, ["e1:c2 already applied"], client=client
        )
    assert fallback_used is False
    assert len(stages) == 2
    assert {c["exception_id"]: c["candidate_id"] for c in choices} == {
        "e1": "c1",
        "e2": "c2",
    }
    # Repair prompt must list the valid ids.
    repair_text = calls[1]["messages"][-1]["content"]
    assert "e1" in repair_text and "c1, c2" in repair_text


@pytest.mark.asyncio
async def test_review_falls_back_to_c1_when_both_attempts_bad():
    bodies = [
        or_response("I would pick whichever seems best."),
        or_response(choices_json([("e1", "nope")])),  # still invalid + missing e2
    ]
    async with make_client(bodies) as client:
        choices, stages, fallback_used = await review_exceptions(
            EXCEPTIONS, {}, [], client=client
        )
    assert fallback_used is True
    assert len(stages) == 2
    assert choices == [
        {"exception_id": "e1", "candidate_id": "c1"},
        {"exception_id": "e2", "candidate_id": "c1"},
    ]


@pytest.mark.asyncio
async def test_external_agent_review_uses_external_stage_and_framing():
    bodies = [or_response(choices_json([("e1", "c1"), ("e2", "c2")]))]
    calls = []
    async with make_client(bodies, calls=calls) as client:
        choices, stages, fallback_used = await external_agent_review(
            EXCEPTIONS, {"board": "x"}, [], client=client
        )
    assert fallback_used is False
    assert stages[0]["stage"] == "review_external"
    system = calls[0]["messages"][0]["content"]
    assert "generate_design" in system and "apply_correction" in system
    assert "legacy CircuitSpec JSON" in system
    assert "submit_skidl_code" in system
    assert {c["exception_id"] for c in choices} == {"e1", "e2"}
