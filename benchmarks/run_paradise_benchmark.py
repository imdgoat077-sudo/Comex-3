#!/usr/bin/env python3
"""
benchmarks/run_paradise_benchmark.py
====================================
Commit C Live Benchmark Runner: Paradise FDFS Ticket Booking Task.

Evaluates the fourth layer of agent correctness:
  Semantic (Jev) → Safety (Comex) → Physical (Actuator) → Task Progress (ProgressController)

Measures:
  - Terminal outcome (e.g. LOGIN_REQUIRED / PAYMENT_REQUIRED boundary)
  - Total steps vs Baseline B (57 steps)
  - Total duration vs Baseline B (227.23s)
  - Gemini duration vs Baseline B (202.98s)
  - ProgressController decomposition time
  - Tier-2 calls and Tier-2 duration
  - Consecutive stall count vs Baseline B (40+)
  - False subtask completion count
  - Subtask sequence and exact completion evidence
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import tempfile
import time
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

# Isolate benchmark graph DB to prevent lock contention
temp_db_dir = tempfile.mkdtemp()
os.environ["GRAPH_DB_PATH"] = str(Path(temp_db_dir) / "paradise_bench.db")

from dotenv import load_dotenv
load_dotenv(dotenv_path=ROOT_DIR / ".env")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("paradise_benchmark")

ELECTRON_CDP_URL = os.getenv("CDP_URL", "http://127.0.0.1:9222")
TASK_PROMPT = os.getenv("BENCHMARK_TASK", "Book FDFS Paradise on Saturday on BookMyShow")


async def run_benchmark():
    logger.info("=" * 80)
    logger.info(f"STARTING COMMIT C LIVE BENCHMARK: '{TASK_PROMPT}'")
    logger.info("=" * 80)

    # 1. Discover active page target in Electron
    try:
        req = urllib.request.urlopen(f"{ELECTRON_CDP_URL}/json/list", timeout=5)
        targets = json.loads(req.read())
    except Exception as e:
        logger.error(f"Failed to connect to Electron CDP at {ELECTRON_CDP_URL}: {e}")
        return

    active_target = None
    for t in targets:
        url = t.get("url", "")
        if t.get("type") == "page" and "localhost:5173" not in url and "cursor_overlay" not in url:
            active_target = t
            break

    if not active_target:
        logger.error("No suitable active page target found in Electron CDP.")
        return

    target_ws = active_target.get("webSocketDebuggerUrl")
    logger.info(f"Connected to target: {active_target.get('id')} | URL: {active_target.get('url')}")
    logger.info(f"CDP WebSocket: {target_ws}")

    # 2. Reset tab to about:blank to ensure clean initial state
    import websockets
    try:
        async with websockets.connect(target_ws, max_size=20 * 1024 * 1024) as ws:
            logger.info("Resetting tab to about:blank for fresh benchmark execution...")
            await ws.send(json.dumps({
                "id": 1,
                "method": "Page.navigate",
                "params": {"url": "about:blank"}
            }))
            await ws.recv()
            await asyncio.sleep(1.0)
    except Exception as e:
        logger.warning(f"Failed to reset tab (proceeding with existing page): {e}")

    # 3. Track events emitted by agent loop
    events = []
    def on_event(event_type, data):
        events.append({"type": event_type, "data": data, "timestamp": time.monotonic()})
        if event_type in ("step_complete", "tool_start", "terminal_boundary"):
            logger.info(f"[EVENT:{event_type}] {data}")

    # 4. Import and run execute_agent_loop
    from agent_service import execute_agent_loop

    t_start = time.monotonic()
    result = await execute_agent_loop(
        task=TASK_PROMPT,
        default_cdp_url=target_ws,
        intent="visible_browser_task",
        use_procedures=False,
        use_graph=True,
        event_callback=on_event,
    )
    t_total = time.monotonic() - t_start

    # 5. Extract comprehensive benchmark telemetry
    meta = result.metadata if hasattr(result, "metadata") else {}
    timing_summary = meta.get("timing_summary", {})
    steps = timing_summary.get("steps", [])
    step_count = len(steps)
    gemini_duration = timing_summary.get("total_gemini_duration_s", 0.0)
    decompose_time = meta.get("progress_controller_decompose_time_s", 0.0)
    tier2_calls = meta.get("progress_controller_tier2_calls", 0)
    tier2_time = meta.get("progress_controller_tier2_time_s", 0.0)
    subtasks = meta.get("progress_controller_subtasks") or []
    terminal_boundary = meta.get("terminal_boundary") or "NONE"
    termination_reason = meta.get("termination_reason") or "UNKNOWN"
    final_text = result.text if hasattr(result, "text") else str(result)

    # Count consecutive stalls / loops
    stalls = 0
    for st in steps:
        if not st.get("success", True):
            stalls += 1

    # Check subtasks completed and exact evidence
    completed_subtasks = [s for s in subtasks if isinstance(s, dict) and s.get("status") == "COMPLETE"]

    # 6. Format and display comparative results table
    print("\n" + "=" * 80)
    print("COMMIT C LIVE PARADISE BENCHMARK RESULTS")
    print("=" * 80)
    print(f"{'Metric':<30} | {'Baseline B':<15} | {'Commit C':<20}")
    print("-" * 72)
    print(f"{'Correct terminal boundary':<30} | {'LOGIN_REQUIRED':<15} | {terminal_boundary:<20}")
    print(f"{'Termination reason':<30} | {'—':<15} | {termination_reason:<20}")
    print(f"{'Total steps':<30} | {'57':<15} | {step_count:<20}")
    print(f"{'Total time':<30} | {'227.23s':<15} | {t_total:.2f}s")
    print(f"{'Gemini time':<30} | {'202.98s':<15} | {gemini_duration:.2f}s")
    print(f"{'Decomposition time':<30} | {'—':<15} | {decompose_time:.3f}s")
    print(f"{'Tier-2 calls':<30} | {'—':<15} | {tier2_calls:<20}")
    print(f"{'Tier-2 time':<30} | {'—':<15} | {tier2_time:.3f}s")
    print(f"{'Failed/stall steps':<30} | {'40+':<15} | {stalls:<20}")
    print(f"{'Subtasks planned':<30} | {'—':<15} | {len(subtasks):<20}")
    print(f"{'Subtasks completed':<30} | {'—':<15} | {len(completed_subtasks):<20}")
    print("=" * 80)

    print("\nSUBTASK EXECUTION & COMPLETION EVIDENCE AUDIT:")
    print("-" * 80)
    for i, s in enumerate(subtasks):
        if isinstance(s, dict):
            status = s.get("status", "UNKNOWN")
            desc = s.get("description", "")
            ev = s.get("completion_evidence", "none")
            attempts = s.get("attempts", 0)
            print(f"  [{i+1}] status={status} | attempts={attempts} | '{desc}'")
            if ev and ev != "none":
                print(f"      -> EVIDENCE: {ev}")
        else:
            print(f"  [{i+1}] {s}")
    print("-" * 80)

    # 7. Save raw benchmark results
    out_dir = ROOT_DIR / "reports"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / "paradise_c_benchmark_results.json"
    with open(out_file, "w") as f:
        json.dump({
            "task": TASK_PROMPT,
            "condition": "commit_c",
            "terminal_boundary": terminal_boundary,
            "termination_reason": termination_reason,
            "total_duration_s": t_total,
            "gemini_duration_s": gemini_duration,
            "step_count": step_count,
            "stalls": stalls,
            "decompose_time_s": decompose_time,
            "tier2_calls": tier2_calls,
            "tier2_time_s": tier2_time,
            "subtasks": subtasks,
            "final_text": final_text[:500],
            "metadata": meta,
        }, f, indent=2, default=str)
    logger.info(f"Saved benchmark results to {out_file}")


if __name__ == "__main__":
    asyncio.run(run_benchmark())
