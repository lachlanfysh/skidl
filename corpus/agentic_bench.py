"""Agentic benchmark: models get marketing copy + MCP tools, figure out the rest.

Unlike stress_test.py (10K system prompt with schema, examples, pin dumps for
single-shot JSON generation), this tests the realistic agentic flow: simple user
prompt -> discover MCP tools -> read resources -> iteratively build and submit.

The model sees exactly what a real MCP client would expose: server instructions,
tool definitions, and readable resources. No spec format, no worked examples,
no IC pin dumps in the prompt.

Usage:
    OPENROUTER_API_KEY=... python3 -m corpus.agentic_bench \
        --server https://mcp-server-production-5d58.up.railway.app/mcp \
        --token $EDA_AUTH_TOKEN \
        --spend-cap 5.0

    # Probe9 subset with one model:
    OPENROUTER_API_KEY=... python3 -m corpus.agentic_bench \
        --server $MCP_URL --token $EDA_AUTH_TOKEN \
        --board probe9 --models google/gemini-2.5-flash \
        --spend-cap 2.0
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from corpus.mcp_ux_probe import MCPClient, openai_tools, shrink_result
from corpus.circuit_judge import score_deterministic
from corpus.quality_score import _grade
from llm.config import price_for
from schemas.enrichment import enrich, enrich_blocks

REPO_ROOT = Path(__file__).resolve().parent.parent

MODELS = [
    "google/gemini-2.5-flash",
    "google/gemini-2.5-pro",
    "openai/gpt-4.1-mini",
]

SYSTEM_PROMPT = """\
You have access to a PCB design service. {instructions}"""

MAX_TURNS = 60
POLL_SPACING_S = 5.0


@dataclass
class AgentRunResult:
    board_id: str
    model: str
    ok: bool
    turns_used: int = 0
    tool_calls_made: int = 0
    resources_read: list = field(default_factory=list)
    specs_submitted: int = 0
    final_spec: dict | None = None
    corrections_made: int = 0
    wall_time_s: float = 0.0
    cost_usd: float = 0.0
    tokens_in: int = 0
    tokens_out: int = 0
    bom_score: float | None = None
    netlist_score: float | None = None
    grade: str | None = None
    enriched_bom_score: float | None = None
    enriched_grade: str | None = None
    enrichment_actions: int = 0
    missing_parts: list | None = None
    extra_parts: list | None = None
    server_status: str | None = None
    error: str = ""
    final_report: str | None = None
    transcript: list | None = None


@dataclass
class BenchMetrics:
    results: list[AgentRunResult] = field(default_factory=list)
    start_time: float = 0.0
    end_time: float = 0.0

    @property
    def total_cost(self) -> float:
        return sum(r.cost_usd for r in self.results)

    @property
    def wall_time(self) -> float:
        return self.end_time - self.start_time


def _load_manifest():
    manifest = REPO_ROOT / "corpus" / "manifest.jsonl"
    boards = []
    with open(manifest) as f:
        for line in f:
            if not line.strip():
                continue
            d = json.loads(line)
            if d.get("nl_source") == "marketing" and d.get("description"):
                boards.append(d)
    return boards


def _load_ref_specs():
    specs_dir = REPO_ROOT / "corpus" / "specs"
    refs = {}
    for f in specs_dir.glob("*.json"):
        if f.stem.startswith("ref-"):
            continue
        refs[f.stem] = json.loads(f.read_text())
    return refs


def _estimate_cost(tokens_in: int, tokens_out: int, model: str) -> float:
    in_price, out_price = price_for(model)
    return tokens_in / 1e6 * in_price + tokens_out / 1e6 * out_price


def _extract_spec_from_args(arguments: dict) -> dict | None:
    """Pull the CircuitSpec dict from submit_design arguments."""
    for key in ("spec", "input_spec", "circuit_spec", "design"):
        val = arguments.get(key)
        if isinstance(val, dict):
            return val
        if isinstance(val, str):
            try:
                parsed = json.loads(val)
                if isinstance(parsed, dict):
                    return parsed
            except (json.JSONDecodeError, TypeError):
                pass
    if "parts" in arguments and "nets" in arguments:
        return arguments
    return None


def run_agent_board(
    board: dict,
    model: str,
    mcp: MCPClient,
    tools: list[dict],
    instructions: str,
    api_key: str,
    artifacts_dir: Path,
    max_turns: int = MAX_TURNS,
) -> AgentRunResult:
    """Run one model on one board through the full agentic MCP flow."""

    board_id = board["board_id"]
    description = board["description"]
    t0 = time.monotonic()

    result = AgentRunResult(board_id=board_id, model=model, ok=False)
    board_arts = artifacts_dir / f"{board_id}__{model.split('/')[-1]}"
    board_arts.mkdir(parents=True, exist_ok=True)

    user_msg = (
        f"I'd like to design a PCB. Here's what I want:\n\n"
        f"{description}\n\n"
        f"Use the available tools to research the design format and submit the circuit."
    )

    messages: list[dict] = [
        {"role": "system", "content": SYSTEM_PROMPT.format(instructions=instructions)},
        {"role": "user", "content": user_msg},
    ]

    or_http = httpx.Client(timeout=300)
    last_poll_at = 0.0
    nudges = 0
    captured_spec: dict | None = None
    turn = 0

    try:
        for turn in range(max_turns):
            body = _openrouter_call(or_http, api_key, model, messages, tools)
            if body is None:
                result.error = "OpenRouter unreachable after retries"
                break
            if isinstance(body, str):
                result.error = body
                break
            if "choices" not in body:
                result.error = f"No choices: {json.dumps(body)[:200]}"
                break

            usage = body.get("usage", {})
            turn_in = usage.get("prompt_tokens", 0)
            turn_out = usage.get("completion_tokens", 0)
            result.tokens_in += turn_in
            result.tokens_out += turn_out

            api_cost = usage.get("cost")
            if isinstance(api_cost, (int, float)) and not isinstance(api_cost, bool):
                result.cost_usd += float(api_cost)
            else:
                result.cost_usd += _estimate_cost(turn_in, turn_out, model)

            msg = body["choices"][0]["message"]
            messages.append(msg)

            tool_calls = msg.get("tool_calls") or []
            content = (msg.get("content") or "").strip()

            if not tool_calls:
                if content.upper().startswith("FINAL REPORT"):
                    result.final_report = content
                    break
                if nudges >= 3:
                    result.final_report = content or "(model stopped without tool use)"
                    break
                messages.append({
                    "role": "user",
                    "content": (
                        "You must use tool calls. Your goal is a SUCCEEDED job. "
                        "If you haven't submitted yet, read eda://guide/workflow "
                        "and eda://guide/circuit-spec first, then submit_design(). "
                        "If your job failed with exceptions, apply_correction() "
                        "for each one and poll again. Keep going until succeeded. "
                        "Only reply with 'FINAL REPORT:' after get_job() shows succeeded."
                    ),
                })
                nudges += 1
                continue
            nudges = 0

            for tc in tool_calls:
                name = tc["function"]["name"]
                result.tool_calls_made += 1

                try:
                    arguments = json.loads(tc["function"]["arguments"] or "{}")
                except json.JSONDecodeError as exc:
                    tool_result = f"TOOL ERROR: invalid JSON: {exc}"
                    arguments = None

                if arguments is not None:
                    if name == "read_resource":
                        result.resources_read.append(arguments.get("uri", ""))

                    if name == "submit_design":
                        result.specs_submitted += 1
                        extracted = _extract_spec_from_args(arguments)
                        if extracted is not None:
                            captured_spec = extracted

                    if name == "apply_correction":
                        result.corrections_made += 1

                    if name == "get_job":
                        wait = POLL_SPACING_S - (time.monotonic() - last_poll_at)
                        if wait > 0:
                            time.sleep(wait)
                        last_poll_at = time.monotonic()

                    try:
                        if name == "read_resource":
                            tool_result = mcp.read_resource(arguments["uri"])
                        else:
                            tool_result = mcp.call_tool(name, arguments)
                    except Exception as exc:
                        tool_result = f"TOOL ERROR: {exc}"

                    if name == "get_job" and isinstance(tool_result, str):
                        try:
                            job_data = json.loads(tool_result)
                            s = job_data.get("status")
                            if s and s not in ("pending", "running"):
                                result.server_status = s
                        except (json.JSONDecodeError, TypeError):
                            pass

                tool_result = shrink_result(name, tool_result, board_arts)
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": tool_result,
                })
    finally:
        or_http.close()

    result.turns_used = turn + 1
    result.wall_time_s = time.monotonic() - t0
    result.final_spec = captured_spec
    result.ok = captured_spec is not None
    result.transcript = messages

    return result


def _openrouter_call(
    http: httpx.Client,
    api_key: str,
    model: str,
    messages: list[dict],
    tools: list[dict],
) -> dict | str | None:
    """Single OpenRouter call with retries. Returns body dict, error string, or None."""
    for attempt in range(3):
        try:
            resp = http.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                json={
                    "model": model,
                    "messages": messages,
                    "tools": tools,
                    "temperature": 0.2,
                    "usage": {"include": True},
                },
            )
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPStatusError as exc:
            code = exc.response.status_code
            if code == 429 or code >= 500:
                time.sleep(10 * (attempt + 1))
                continue
            if code == 400 and attempt < 2:
                while messages and messages[-1]["role"] in ("assistant", "tool"):
                    messages.pop()
                messages.append({
                    "role": "user",
                    "content": "Continue with the design task. Use tool calls.",
                })
                continue
            return f"HTTP {code}: {exc.response.text[:300]}"
        except httpx.HTTPError:
            time.sleep(5 * (attempt + 1))
            continue
    return None


def score_result(result: AgentRunResult, ref_specs: dict) -> None:
    """Score a result's captured spec against the reference, mutating in place."""
    if result.final_spec is None:
        return
    ref = ref_specs.get(result.board_id)
    if ref is None:
        return

    try:
        det = score_deterministic(result.final_spec, ref)
    except Exception:
        return

    combined = 0.4 * det.bom_score + 0.4 * det.netlist_score + 0.2 * det.structural_score
    result.bom_score = det.bom_score
    result.netlist_score = det.netlist_score
    result.grade = _grade(combined)
    result.missing_parts = det.missing_parts
    result.extra_parts = det.extra_parts

    try:
        block_spec, block_actions = enrich_blocks(result.final_spec, "")
        enriched_spec, enrichment_actions = enrich(block_spec)
        all_actions = block_actions + enrichment_actions
        if all_actions:
            det_e = score_deterministic(enriched_spec, ref)
            combined_e = 0.4 * det_e.bom_score + 0.4 * det_e.netlist_score + 0.2 * det_e.structural_score
            result.enriched_bom_score = det_e.bom_score
            result.enriched_grade = _grade(combined_e)
            result.enrichment_actions = len(all_actions)
    except Exception:
        pass


def _print_live(idx: int, total: int, result: AgentRunResult, cumulative_cost: float):
    model_short = result.model.split("/")[-1][:20]
    grade_str = result.grade or "---"
    bom_str = f"{result.bom_score:.2f}" if result.bom_score is not None else "-.--"
    status = "OK" if result.ok else "FAIL"
    srv = result.server_status or "?"
    enrich_str = ""
    if result.enriched_grade and result.enriched_grade != result.grade:
        enrich_str = f" ->{result.enriched_grade}(+{result.enrichment_actions})"
    res_str = ",".join(r.split("/")[-1][:10] for r in result.resources_read[:3])
    print(
        f"[{idx:>3}/{total}] {result.board_id:30s} {model_short:20s} "
        f"{status:4s} srv={srv:10s} {grade_str:>3s}{enrich_str:12s} bom={bom_str} "
        f"turns={result.turns_used:>2d} tools={result.tool_calls_made:>2d} "
        f"res=[{res_str}] "
        f"{result.wall_time_s:5.1f}s ${result.cost_usd:.4f}  "
        f"(total: ${cumulative_cost:.3f})",
        flush=True,
    )


def run_benchmark(
    server_url: str,
    auth_token: str,
    models: list[str],
    board_filter: str | None = None,
    limit: int | None = None,
    spend_cap: float = 10.0,
    max_turns: int = MAX_TURNS,
) -> BenchMetrics:
    boards = _load_manifest()
    if board_filter == "probe9":
        probe_ids = {
            "bme280", "mcp9808", "ina219", "ads1115-adc", "max98357-i2s-amp",
            "feather-rp2040", "feather-esp32-s3", "clue-nrf52840", "grand-central",
        }
        boards = [b for b in boards if b["board_id"] in probe_ids]
    elif board_filter:
        boards = [b for b in boards if board_filter in b["board_id"]]

    ref_specs = _load_ref_specs()
    api_key = os.environ["OPENROUTER_API_KEY"]

    work = [(board, model) for model in models for board in boards]
    if limit:
        work = work[:limit]

    print(f"Agentic benchmark: {len(work)} runs, cap=${spend_cap:.2f}")
    print(f"Models: {[m.split('/')[-1] for m in models]}")
    print(f"Boards: {len(boards)}, ref specs: {len(ref_specs)}")
    print(f"MCP server: {server_url}")
    print("-" * 120)

    mcp = MCPClient(server_url, auth_token)
    for attempt in range(3):
        try:
            init = mcp.connect()
            break
        except Exception as exc:
            print(f"MCP connect attempt {attempt + 1} failed: {exc}", flush=True)
            if attempt < 2:
                time.sleep(5)
            else:
                print("Cannot connect to MCP server", file=sys.stderr)
                sys.exit(1)

    instructions = init.get("instructions", "(none)")
    mcp_tools = mcp.list_tools()
    mcp_resources = mcp.list_resources()
    tools = openai_tools(mcp_tools, mcp_resources)

    print(f"MCP tools: {[t['name'] for t in mcp_tools]}")
    print(f"MCP resources: {[r['uri'] for r in mcp_resources]}")
    print("-" * 120)

    artifacts_dir = REPO_ROOT / "artifacts" / "agentic_bench"
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    metrics = BenchMetrics(start_time=time.monotonic())
    completed = 0
    total = len(work)

    for board, model in work:
        if metrics.total_cost >= spend_cap:
            print(f"\nBudget cap ${spend_cap:.2f} reached, stopping.")
            break

        result = run_agent_board(
            board=board,
            model=model,
            mcp=mcp,
            tools=tools,
            instructions=instructions,
            api_key=api_key,
            artifacts_dir=artifacts_dir,
            max_turns=max_turns,
        )
        score_result(result, ref_specs)
        metrics.results.append(result)

        completed += 1
        _print_live(completed, total, result, metrics.total_cost)

    metrics.end_time = time.monotonic()
    return metrics


def print_report(metrics: BenchMetrics):
    print("\n" + "=" * 120)
    print(f"AGENTIC BENCHMARK COMPLETE")
    print(f"  Wall time: {metrics.wall_time:.1f}s")
    print(f"  Total runs: {len(metrics.results)}")
    print(f"  Success rate: {sum(1 for r in metrics.results if r.ok) / max(len(metrics.results), 1):.1%}")
    srv_counts = Counter(r.server_status or "none" for r in metrics.results)
    print(f"  Server status: {dict(srv_counts)}")
    print(f"  Total cost: ${metrics.total_cost:.4f}")

    by_model: dict[str, list[AgentRunResult]] = {}
    for r in metrics.results:
        by_model.setdefault(r.model, []).append(r)

    print(f"\n{'Model':<25} {'Runs':>4} {'OK%':>4} {'Cost':>8} "
          f"{'BOM':>6} {'Grade':>15} {'Turns':>6} {'Tools':>6} {'Res':>4}")
    print("-" * 110)

    for model in sorted(by_model):
        runs = by_model[model]
        ok = sum(1 for r in runs if r.ok)
        cost = sum(r.cost_usd for r in runs)
        scored = [r for r in runs if r.bom_score is not None]
        avg_bom = sum(r.bom_score for r in scored) / len(scored) if scored else 0
        grades = Counter(r.grade for r in runs if r.grade)
        grade_str = " ".join(f"{g}:{c}" for g, c in sorted(grades.items()))
        avg_turns = sum(r.turns_used for r in runs) / len(runs)
        avg_tools = sum(r.tool_calls_made for r in runs) / len(runs)
        avg_res = sum(len(r.resources_read) for r in runs) / len(runs)
        short = model.split("/")[-1]

        print(f"{short:<25} {len(runs):>4} {ok / len(runs):>4.0%} ${cost:>7.3f} "
              f"{avg_bom:>6.3f} {grade_str:>15} {avg_turns:>6.1f} {avg_tools:>6.1f} {avg_res:>4.1f}")

        enriched_scored = [r for r in runs if r.enriched_bom_score is not None]
        if enriched_scored:
            avg_e_bom = sum(r.enriched_bom_score for r in enriched_scored) / len(enriched_scored)
            e_grades = Counter(r.enriched_grade for r in runs if r.enriched_grade)
            e_str = " ".join(f"{g}:{c}" for g, c in sorted(e_grades.items()))
            print(f"  {'+ enriched':<23} {'':>4} {'':>4} {'':>8} "
                  f"{avg_e_bom:>6.3f} {e_str:>15}")

    by_board: dict[str, list[AgentRunResult]] = {}
    for r in metrics.results:
        if r.bom_score is not None:
            by_board.setdefault(r.board_id, []).append(r)

    if by_board:
        board_avgs = {
            b: sum(r.bom_score for r in rs) / len(rs)
            for b, rs in by_board.items()
        }
        sorted_boards = sorted(board_avgs.items(), key=lambda x: x[1], reverse=True)
        print(f"\nPer-board results:")
        for b, avg in sorted_boards:
            runs = by_board[b]
            grades = " ".join(r.grade or "?" for r in runs)
            resources = set()
            for r in runs:
                resources.update(r.resources_read)
            res_str = ", ".join(sorted(r.split("/")[-1] for r in resources)[:4])
            print(f"  {b:<30} bom={avg:.3f}  grades=[{grades}]  resources=[{res_str}]")

    failed = [r for r in metrics.results if not r.ok]
    if failed:
        print(f"\nFailed runs ({len(failed)}):")
        for r in failed:
            short = r.model.split("/")[-1]
            print(f"  {r.board_id} ({short}): {r.error[:100]}")


def save_results(metrics: BenchMetrics):
    out_dir = REPO_ROOT / "artifacts" / "agentic_bench"
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%d-%H%M%S")
    run_dir = out_dir / f"run-{ts}"
    run_dir.mkdir(parents=True, exist_ok=True)

    for r in metrics.results:
        model_short = r.model.split("/")[-1]
        fname = run_dir / f"{r.board_id}__{model_short}.json"
        data = {
            "board_id": r.board_id,
            "model": r.model,
            "ok": r.ok,
            "turns_used": r.turns_used,
            "tool_calls_made": r.tool_calls_made,
            "resources_read": r.resources_read,
            "specs_submitted": r.specs_submitted,
            "corrections_made": r.corrections_made,
            "wall_time_s": r.wall_time_s,
            "cost_usd": r.cost_usd,
            "tokens_in": r.tokens_in,
            "tokens_out": r.tokens_out,
            "bom_score": r.bom_score,
            "netlist_score": r.netlist_score,
            "grade": r.grade,
            "enriched_bom_score": r.enriched_bom_score,
            "enriched_grade": r.enriched_grade,
            "enrichment_actions": r.enrichment_actions,
            "missing_parts": r.missing_parts,
            "extra_parts": r.extra_parts,
            "server_status": r.server_status,
            "error": r.error,
            "final_report": r.final_report,
            "final_spec": r.final_spec,
            "transcript": r.transcript,
        }
        fname.write_text(json.dumps(data, indent=2))

    summary_file = run_dir / "summary.json"
    summary = {
        "timestamp": ts,
        "wall_time_s": metrics.wall_time,
        "total_runs": len(metrics.results),
        "total_cost_usd": metrics.total_cost,
        "success_rate": sum(1 for r in metrics.results if r.ok) / max(len(metrics.results), 1),
        "results": [
            {
                "board_id": r.board_id,
                "model": r.model,
                "ok": r.ok,
                "grade": r.grade,
                "bom_score": r.bom_score,
                "enriched_grade": r.enriched_grade,
                "cost_usd": r.cost_usd,
                "turns_used": r.turns_used,
                "tool_calls_made": r.tool_calls_made,
                "resources_read": r.resources_read,
                "specs_submitted": r.specs_submitted,
                "corrections_made": r.corrections_made,
                "server_status": r.server_status,
                "error": r.error,
            }
            for r in metrics.results
        ],
    }
    summary_file.write_text(json.dumps(summary, indent=2))
    print(f"\nResults saved: {run_dir}/ ({len(metrics.results)} run files + summary.json)")


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--server", required=True, help="MCP server URL")
    parser.add_argument("--token", required=True, help="MCP auth token")
    parser.add_argument("--models", nargs="+", default=None,
                        help=f"Model IDs (default: {[m.split('/')[-1] for m in MODELS]})")
    parser.add_argument("--board", type=str, default=None,
                        help="Filter boards by substring, or 'probe9' for the 9-board subset")
    parser.add_argument("--limit", type=int, default=None,
                        help="Max total runs")
    parser.add_argument("--spend-cap", type=float, default=5.0,
                        help="Stop after this much estimated spend (USD)")
    parser.add_argument("--max-turns", type=int, default=MAX_TURNS,
                        help="Max agent turns per board")
    args = parser.parse_args()

    if not os.environ.get("OPENROUTER_API_KEY"):
        print("ERROR: OPENROUTER_API_KEY not set", file=sys.stderr)
        sys.exit(1)

    models = args.models or MODELS
    metrics = run_benchmark(
        server_url=args.server,
        auth_token=args.token,
        models=models,
        board_filter=args.board,
        limit=args.limit,
        spend_cap=args.spend_cap,
        max_turns=args.max_turns,
    )
    print_report(metrics)
    save_results(metrics)


if __name__ == "__main__":
    main()
