"""Overnight batch runner: diverse models x diverse circuits x 3 prompt tiers.

Drives mcp_ux_probe.py (real MCP client) with --no-system-prompt for each
(model, board, tier) combination. User prompt only — no coaching.

Usage:
    OPENROUTER_API_KEY=sk-or-... python3 -m corpus.batch_mcp_bench \
        --out /tmp/bench-overnight \
        --server https://mcp-server-production-5d58.up.railway.app/mcp \
        --token $EDA_AUTH_TOKEN \
        [--models deepseek/deepseek-v3.2,openai/gpt-4.1-nano] \
        [--boards bme280,ina219] \
        [--tiers naive,marketing] \
        [--max-runs 200] \
        [--parallel 3]
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from threading import Lock

REPO_ROOT = Path(__file__).resolve().parent.parent

DEFAULT_MODELS = [
    "deepseek/deepseek-v4-flash",
    "deepseek/deepseek-v3.2",
    "qwen/qwen3-235b-a22b-2507",
    "qwen/qwen3-32b",
    "google/gemini-2.5-flash-lite",
    "openai/gpt-4.1-nano",
    "openai/gpt-4.1-mini",
]

ALL_TIERS = ("naive", "marketing", "ee_spec")


def load_prompts() -> list[dict]:
    prompts = []
    manifest = REPO_ROOT / "corpus" / "manifest.jsonl"
    with open(manifest) as f:
        for line in f:
            if not line.strip():
                continue
            d = json.loads(line)
            if d.get("nl_source") == "marketing" and d.get("description"):
                prompts.append({
                    "board_id": d["board_id"],
                    "tier": "marketing",
                    "description": d["description"],
                })

    nl_tiers = REPO_ROOT / "corpus" / "nl_tiers.jsonl"
    if nl_tiers.exists():
        with open(nl_tiers) as f:
            for line in f:
                if not line.strip():
                    continue
                d = json.loads(line)
                prompts.append({
                    "board_id": d["board_id"],
                    "tier": d["nl_source"],
                    "description": d["description"],
                })
    return prompts


def build_work_queue(
    prompts: list[dict],
    models: list[str],
    tiers: tuple[str, ...],
    boards: list[str] | None,
    completed: set[str],
) -> list[dict]:
    filtered = [p for p in prompts if p["tier"] in tiers]
    if boards:
        filtered = [p for p in filtered if p["board_id"] in boards]

    work = []
    for model in models:
        for p in filtered:
            key = f"{model}|{p['board_id']}|{p['tier']}"
            if key not in completed:
                work.append({"model": model, **p, "key": key})
    return work


def load_completed(out_dir: Path) -> set[str]:
    done = set()
    results_file = out_dir / "results.jsonl"
    if results_file.exists():
        with open(results_file) as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    r = json.loads(line)
                    done.add(r["key"])
                except (json.JSONDecodeError, KeyError):
                    pass
    return done


def run_one(item: dict, server: str, token: str, out_dir: Path) -> dict:
    model = item["model"]
    board_id = item["board_id"]
    tier = item["tier"]
    description = item["description"]

    model_short = model.split("/")[-1]
    run_dir = out_dir / model_short / f"{board_id}_{tier}"

    request = f"Design me a {board_id} board: {description}"

    env = os.environ.copy()
    env["PYTHONPATH"] = str(REPO_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    cmd = [
        sys.executable, "-m", "corpus.mcp_ux_probe",
        "--model", model,
        "--server", server,
        "--token", token,
        "--out", str(run_dir),
        "--request", request,
        "--no-system-prompt",
    ]

    started = time.time()
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True,
            timeout=900, env=env, cwd=str(REPO_ROOT),
        )
        wall = time.time() - started
        stdout = proc.stdout
        stderr = proc.stderr
        exit_code = proc.returncode
    except subprocess.TimeoutExpired:
        wall = time.time() - started
        stdout = ""
        stderr = "TIMEOUT after 900s"
        exit_code = -1

    summary_file = run_dir / "summary.json"
    summary = {}
    if summary_file.exists():
        try:
            summary = json.loads(summary_file.read_text())
        except json.JSONDecodeError:
            pass

    artifacts = summary.get("artifacts_fetched", [])
    has_pcb = any("kicad_pcb" in a for a in artifacts)
    has_sch = any("kicad_sch" in a for a in artifacts)

    result = {
        "key": item["key"],
        "model": model,
        "board_id": board_id,
        "tier": tier,
        "exit_code": exit_code,
        "wall_s": round(wall, 1),
        "turns": summary.get("turns_used", 0),
        "prompt_tokens": summary.get("usage", {}).get("prompt_tokens", 0),
        "completion_tokens": summary.get("usage", {}).get("completion_tokens", 0),
        "has_pcb": has_pcb,
        "has_sch": has_sch,
        "artifacts": artifacts,
        "finished_report": summary.get("finished_with_report", False),
        "error": stderr[:500] if exit_code != 0 else None,
    }
    return result


def print_scoreboard(results: list[dict]):
    by_model = defaultdict(lambda: {"total": 0, "pcb": 0, "sch": 0, "tokens": 0})
    for r in results:
        m = by_model[r["model"]]
        m["total"] += 1
        if r["has_pcb"]:
            m["pcb"] += 1
        if r["has_sch"]:
            m["sch"] += 1
        m["tokens"] += r.get("completion_tokens", 0)

    print("\n=== Scoreboard ===")
    print(f"{'Model':<40s} {'Runs':>5s} {'PCB':>5s} {'SCH':>5s} {'Rate':>6s} {'Tokens':>8s}")
    print("-" * 70)
    for model in sorted(by_model, key=lambda m: by_model[m]["pcb"] / max(by_model[m]["total"], 1), reverse=True):
        m = by_model[model]
        rate = m["pcb"] / m["total"] * 100 if m["total"] else 0
        print(f"{model:<40s} {m['total']:>5d} {m['pcb']:>5d} {m['sch']:>5d} {rate:>5.0f}% {m['tokens']:>8,d}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--server", required=True)
    ap.add_argument("--token", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--models", default=None,
                    help="Comma-separated model IDs (default: all 8 cheap models)")
    ap.add_argument("--boards", default=None,
                    help="Comma-separated board IDs (default: all)")
    ap.add_argument("--tiers", default="naive,marketing,ee_spec",
                    help="Comma-separated tiers (default: all 3)")
    ap.add_argument("--max-runs", type=int, default=0,
                    help="Stop after N runs (0 = unlimited)")
    ap.add_argument("--shuffle", action="store_true",
                    help="Randomize work order for diversity")
    ap.add_argument("--parallel", type=int, default=6,
                    help="Concurrent runs (default 6)")
    args = ap.parse_args()

    if not os.environ.get("OPENROUTER_API_KEY"):
        print("OPENROUTER_API_KEY not set", file=sys.stderr)
        return 1

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    results_file = out / "results.jsonl"

    models = args.models.split(",") if args.models else DEFAULT_MODELS
    tiers = tuple(args.tiers.split(","))
    boards = args.boards.split(",") if args.boards else None

    prompts = load_prompts()
    completed = load_completed(out)
    work = build_work_queue(prompts, models, tiers, boards, completed)

    if args.shuffle:
        import random
        random.shuffle(work)

    print(f"Models: {len(models)}, Boards: {len(set(w['board_id'] for w in work))}, "
          f"Tiers: {tiers}")
    print(f"Total work items: {len(work)} (skipping {len(completed)} already done)")

    if not work:
        print("Nothing to do.")
        if completed:
            all_results = []
            with open(results_file) as f:
                for line in f:
                    if line.strip():
                        all_results.append(json.loads(line))
            print_scoreboard(all_results)
        return 0

    max_runs = args.max_runs if args.max_runs > 0 else len(work)
    all_results = []
    if results_file.exists():
        with open(results_file) as f:
            for line in f:
                if line.strip():
                    all_results.append(json.loads(line))

    write_lock = Lock()
    counter = {"done": 0}
    batch = work[:max_runs]

    def do_run(item):
        result = run_one(item, args.server, args.token, out)
        with write_lock:
            all_results.append(result)
            with open(results_file, "a") as f:
                f.write(json.dumps(result) + "\n")
            counter["done"] += 1
            n = counter["done"]
            status = "PCB" if result["has_pcb"] else ("SCH" if result["has_sch"] else "FAIL")
            model_short = item["model"].split("/")[-1]
            print(f"[{n}/{len(batch)}] {status} | {model_short} | "
                  f"{item['board_id']}:{item['tier']} | "
                  f"{result['wall_s']}s {result['turns']}t", flush=True)
            if n % 20 == 0:
                print_scoreboard(all_results)
        return result

    print(f"Launching {len(batch)} runs across {args.parallel} workers...", flush=True)
    with ThreadPoolExecutor(max_workers=args.parallel) as pool:
        futures = [pool.submit(do_run, item) for item in batch]
        for f in as_completed(futures):
            try:
                f.result()
            except Exception as exc:
                print(f"  CRASH: {exc}", flush=True)

    print_scoreboard(all_results)
    return 0


if __name__ == "__main__":
    sys.exit(main())
