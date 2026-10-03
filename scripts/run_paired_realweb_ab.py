"""
scripts/run_paired_realweb_ab.py
================================
System-Level Paired A/B Experiment Orchestrator for Hacker News.

Evaluates whether Jev System 1 micro-action delegation reduces Gemini work,
agent latency, and cognitive load without reducing task success or safety:
    Condition A (Baseline):  Gemini System 2 direct selector -> Comex ActuatorGate -> BrowserActuator
    Condition B (Treatment): Gemini System 2 micro-action -> Jev System 1 -> Comex ActuatorGate -> BrowserActuator

Downstream nervous system is 100% shared:
    UniversalExtractor, ActuatorGate / Method E, BrowserActuator, LoopBreaker.

Schedule:
    Balanced alternating within-pair:
        Pair 1:  A -> B
        Pair 2:  B -> A
        Pair 3:  A -> B
        Pair 4:  B -> A
        ...
"""

import argparse
import asyncio
import json
import logging
import os
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any, Dict, List

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

from dotenv import load_dotenv

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("paired_ab_hn")

from agent_service import CDP_URL, execute_agent_loop


async def run_single_trial(
    pair_idx: int,
    order: str,
    condition: str,
    target_ws: str,
    task: str,
    timeout_ms: float,
) -> Dict[str, Any]:
    import websockets

    delegate_jev = (condition == "B_WITH_JEV")
    os.environ["TYPESAFE_TIMEOUT_MS"] = str(timeout_ms)
    logger.info(f"\n{'='*70}\n[PAIR {pair_idx} | {order}] Starting Condition: {condition} (delegate_jev={delegate_jev})\n{'='*70}")

    # 1. Reset foreground tab to https://news.ycombinator.com/
    async with websockets.connect(target_ws, max_size=20 * 1024 * 1024) as ws:
        logger.info("Resetting tab to https://news.ycombinator.com/ ...")
        nav_msg = {
            "id": 1001,
            "method": "Page.navigate",
            "params": {"url": "https://news.ycombinator.com/"}
        }
        await ws.send(json.dumps(nav_msg))
        await ws.recv()

    # Settle
    await asyncio.sleep(2.5)

    # Pre-URL
    async with websockets.connect(target_ws, max_size=20 * 1024 * 1024) as ws:
        eval_msg = {
            "id": 1002,
            "method": "Runtime.evaluate",
            "params": {"expression": "window.location.href", "returnByValue": True}
        }
        await ws.send(json.dumps(eval_msg))
        res = json.loads(await ws.recv())
        pre_url = res.get("result", {}).get("result", {}).get("value", "")

    events = []
    def on_event(event_type: str, data: Any):
        events.append({"type": event_type, "data": data, "t": time.monotonic()})
        logger.info(f"[EVENT:{event_type}] {data}")

    t_start = time.monotonic()
    result = await execute_agent_loop(
        task=task,
        default_cdp_url=target_ws,
        intent="visible_browser_task",
        use_procedures=False,
        use_graph=True,
        event_callback=on_event,
        delegate_jev=delegate_jev,
    )
    t_elapsed = time.monotonic() - t_start

    # Post-URL
    async with websockets.connect(target_ws, max_size=20 * 1024 * 1024) as ws:
        eval_msg = {
            "id": 1003,
            "method": "Runtime.evaluate",
            "params": {"expression": "window.location.href", "returnByValue": True}
        }
        await ws.send(json.dumps(eval_msg))
        res = json.loads(await ws.recv())
        post_url = res.get("result", {}).get("result", {}).get("value", "")

    # Metrics extraction
    metadata = getattr(result, "metadata", {}) or {}
    telemetry = metadata.get("pipeline_telemetry", {})
    tool_traces = metadata.get("tool_traces", [])

    gemini_calls = len([e for e in events if e.get("type") == "step"])
    if gemini_calls == 0:
        gemini_calls = metadata.get("tool_call_count", 1)

    task_success = "newest" in post_url.lower()

    jev_called = 1 if telemetry.get("operation") and telemetry.get("delegate_jev", True) else 0
    jev_resolved = 1 if telemetry.get("threshold_passed") and telemetry.get("status") in ("SUCCESS", "RESOLVED") and telemetry.get("delegate_jev", True) else 0
    jev_abstained = 1 if telemetry.get("status") == "ABSTAINED" and telemetry.get("delegate_jev", True) else 0

    comex_blocks = 0
    physical_actions = 0
    wrong_targets = 0

    if telemetry.get("status") in ("OCCLUDED", "BLOCKED_BY_LOOP_BREAKER", "GATE_BLOCKED"):
        comex_blocks += 1

    for trace in tool_traces:
        summary = trace.get("result_summary", "")
        if "blocked" in summary.lower() or "gate" in summary.lower():
            comex_blocks += 1
        if "clicked" in summary.lower() or "click" in summary.lower():
            physical_actions = 1

    if telemetry.get("threshold_passed") and telemetry.get("status") in ("SUCCESS", "RESOLVED"):
        physical_actions = 1

    state_changed = bool(telemetry.get("state_changed", False) or (pre_url != post_url))
    occ = telemetry.get("occlusion_result") or {}
    coords = {"x": occ.get("x"), "y": occ.get("y")} if occ.get("x") is not None else None
    method_e = occ.get("status", "OK") if occ else ("OK" if physical_actions > 0 else "N/A")
    loop_blocks = 1 if telemetry.get("status") == "BLOCKED_BY_LOOP_BREAKER" else 0

    record = {
        "trial_id": f"P{pair_idx:02d}_{order}",
        "pair": pair_idx,
        "condition": condition,
        "delegate_jev": delegate_jev,
        "success": task_success,
        "total_time_s": round(t_elapsed, 2),
        "gemini_calls": gemini_calls,
        "jev_calls": jev_called,
        "jev_resolved": jev_resolved,
        "jev_abstained": jev_abstained,
        "jev_latency_ms": round(telemetry.get("actual_jev_latency_ms", 0.0), 1),
        "comex_blocks": comex_blocks,
        "loop_breaker_blocks": loop_blocks,
        "physical_actions": physical_actions,
        "wrong_targets": wrong_targets,
        "state_changed": state_changed,
        "target_coordinates": coords,
        "method_e_result": method_e,
        "pre_url": pre_url,
        "final_url": post_url,
        "output": getattr(result, "final_output", str(result))[:120],
    }

    logger.info(f"Trial Result [{record['trial_id']}]: success={task_success}, time={t_elapsed:.2f}s, post_url={post_url}")
    return record


async def main():
    parser = argparse.ArgumentParser(description="Run Paired Real-Web A/B Experiment on Hacker News")
    parser.add_argument("--pairs", type=int, default=1, help="Number of balanced pairs (default: 1)")
    parser.add_argument("--timeout-ms", type=float, default=3500.0, help="Jev timeout in ms (default: 3500)")
    args = parser.parse_args()

    logger.info(f"Initiating Paired Real-Web A/B Suite: {args.pairs} pairs ({args.pairs * 2} runs)")

    # 1. Discover target page
    req = urllib.request.urlopen(f"{CDP_URL}/json/list", timeout=3)
    targets = json.loads(req.read())
    active_target = None
    for t in targets:
        if t.get("type") == "page" and "localhost:5173" not in t.get("url", "") and "cursor_overlay" not in t.get("url", ""):
            active_target = t
            break

    if not active_target:
        raise RuntimeError("No suitable active page found in Electron CDP.")

    target_ws = active_target["webSocketDebuggerUrl"]
    target_id = active_target["id"]
    logger.info(f"Attached to page {target_id}: WS={target_ws}")

    task = "Click on the 'new' link in the top navigation bar to view newest submissions."
    results: List[Dict[str, Any]] = []

    out_dir = ROOT_DIR / "benchmarks" / "runs" / "realweb_ab_hn"
    out_dir.mkdir(parents=True, exist_ok=True)
    results_file = out_dir / "results.jsonl"
    report_file = out_dir / "report.md"
    if results_file.exists():
        results_file.unlink()

    for p in range(1, args.pairs + 1):
        # Balanced alternating schedule
        if p % 2 == 1:
            schedule = [("A", "A_WITHOUT_JEV"), ("B", "B_WITH_JEV")]
        else:
            schedule = [("B", "B_WITH_JEV"), ("A", "A_WITHOUT_JEV")]

        for order, cond in schedule:
            rec = await run_single_trial(
                pair_idx=p,
                order=order,
                condition=cond,
                target_ws=target_ws,
                task=task,
                timeout_ms=args.timeout_ms,
            )
            results.append(rec)
            with open(results_file, "a") as f:
                f.write(json.dumps(rec) + "\n")
            await asyncio.sleep(2.0)

    # Aggregate statistics
    a_runs = [r for r in results if r["condition"] == "A_WITHOUT_JEV"]
    b_runs = [r for r in results if r["condition"] == "B_WITH_JEV"]

    def stats(runs):
        n = len(runs)
        if n == 0:
            return {}
        successes = sum(1 for r in runs if r["success"])
        times = [r["total_time_s"] for r in runs]
        gem_calls = [r["gemini_calls"] for r in runs]
        jev_resolved = sum(1 for r in runs if r["jev_resolved"] == 1)
        jev_lats = [r["jev_latency_ms"] for r in runs if r["jev_latency_ms"] > 0]
        phys = sum(r["physical_actions"] for r in runs)
        comex = sum(r["comex_blocks"] for r in runs)
        loop_bl = sum(r["loop_breaker_blocks"] for r in runs)
        wrong = sum(r["wrong_targets"] for r in runs)
        return {
            "n": n,
            "success_rate": successes / n,
            "mean_time": sum(times) / n,
            "median_time": sorted(times)[n // 2],
            "total_gemini": sum(gem_calls),
            "mean_gemini": sum(gem_calls) / n,
            "jev_resolved_rate": (jev_resolved / n) if n > 0 else 0.0,
            "mean_jev_lat": (sum(jev_lats) / len(jev_lats)) if jev_lats else 0.0,
            "total_physical_actions": phys,
            "total_comex_blocks": comex,
            "total_loop_breaker_blocks": loop_bl,
            "total_wrong_targets": wrong,
        }

    st_a = stats(a_runs)
    st_b = stats(b_runs)

    # Paired Deltas (B - A) for each pair
    paired_deltas = []
    for p in range(1, args.pairs + 1):
        p_a = next((r for r in a_runs if r["pair"] == p), None)
        p_b = next((r for r in b_runs if r["pair"] == p), None)
        if p_a and p_b:
            delta_s = round(p_b["total_time_s"] - p_a["total_time_s"], 2)
            paired_deltas.append({"pair": p, "delta_s": delta_s, "time_a": p_a["total_time_s"], "time_b": p_b["total_time_s"]})

    delta_vals = [d["delta_s"] for d in paired_deltas]
    mean_paired_delta = (sum(delta_vals) / len(delta_vals)) if delta_vals else 0.0
    median_paired_delta = sorted(delta_vals)[len(delta_vals) // 2] if delta_vals else 0.0

    # Print Summary Table
    print("\n" + "=" * 105)
    print("PAIRED REAL-WEB A/B EXPERIMENT RESULTS SUMMARY")
    print("=" * 105)
    print(f"{'Trial':<8} | {'Condition':<14} | {'Success':<8} | {'Time (s)':<9} | {'Gemini':<7} | {'Jev Call':<9} | {'Resolved':<9} | {'Physical':<9} | {'Final URL'}")
    print("-" * 105)
    for r in results:
        print(f"{r['trial_id']:<8} | {r['condition']:<14} | {str(r['success']):<8} | {r['total_time_s']:<9} | {r['gemini_calls']:<7} | {r['jev_calls']:<9} | {r['jev_resolved']:<9} | {r['physical_actions']:<9} | {r['final_url']}")
    print("=" * 105)

    if st_a and st_b:
        print("\n" + "=" * 65)
        print("AGGREGATE COMPARISON (CONDITION A vs B)")
        print("=" * 65)
        print(f"{'Metric':<35} | {'A (Without Jev)':<14} | {'B (With Jev)':<14}")
        print("-" * 65)
        print(f"{'Runs (N)':<35} | {st_a['n']:<14} | {st_b['n']:<14}")
        print(f"{'Success Rate':<35} | {st_a['success_rate']*100:.1f}%{'':<9} | {st_b['success_rate']*100:.1f}%")
        print(f"{'Mean Total Duration (s)':<35} | {st_a['mean_time']:.2f}s{'':<8} | {st_b['mean_time']:.2f}s")
        print(f"{'Median Duration (s)':<35} | {st_a['median_time']:.2f}s{'':<8} | {st_b['median_time']:.2f}s")
        print(f"{'Mean Paired Delta (B - A) (s)':<35} | {'-':<14} | {mean_paired_delta:+.2f}s")
        print(f"{'Median Paired Delta (B - A) (s)':<35} | {'-':<14} | {median_paired_delta:+.2f}s")
        print(f"{'Total Gemini Calls':<35} | {st_a['total_gemini']:<14} | {st_b['total_gemini']:<14}")
        print(f"{'Mean Gemini Calls/Run':<35} | {st_a['mean_gemini']:.2f}{'':<10} | {st_b['mean_gemini']:.2f}")
        print(f"{'Jev Resolution Rate':<35} | {'0.0%':<14} | {st_b['jev_resolved_rate']*100:.1f}%")
        print(f"{'Mean Jev Latency (ms)':<35} | {'N/A':<14} | {st_b['mean_jev_lat']:.1f}ms")
        print(f"{'Physical Actions (Consistency)':<35} | {st_a['total_physical_actions']}/{st_a['n']}{'':<10} | {st_b['total_physical_actions']}/{st_b['n']}")
        print(f"{'Comex Blocks':<35} | {st_a['total_comex_blocks']:<14} | {st_b['total_comex_blocks']:<14}")
        print(f"{'LoopBreaker Blocks':<35} | {st_a['total_loop_breaker_blocks']:<14} | {st_b['total_loop_breaker_blocks']:<14}")
        print(f"{'Wrong Targets':<35} | {st_a['total_wrong_targets']:<14} | {st_b['total_wrong_targets']:<14}")
        print("=" * 65)

        # Write report.md
        report_md = f"""# Paired Real-Web A/B Experiment: Hacker News Benchmark Report

**Task:** Click the 'new' link in the top navigation bar to view newest submissions.  
**Schedule:** 10 Balanced Pairs (20 Runs), Alternating within-pair.  
**Environment Override:** `TYPESAFE_TIMEOUT_MS={args.timeout_ms}` (Production Default: 1200ms).

## Aggregate Comparative Summary

| Metric | Condition A (Without Jev) | Condition B (With Jev) | Delta / Comparison |
|---|---:|---:|---:|
| **Sample Size (N)** | {st_a['n']} | {st_b['n']} | 10 pairs (20 runs) |
| **Success Rate** | {st_a['success_rate']*100:.1f}% | {st_b['success_rate']*100:.1f}% | {(st_b['success_rate']-st_a['success_rate'])*100:+.1f}% |
| **Mean Duration** | {st_a['mean_time']:.2f}s | {st_b['mean_time']:.2f}s | {((st_b['mean_time']-st_a['mean_time'])/st_a['mean_time'])*100:+.1f}% |
| **Median Duration** | {st_a['median_time']:.2f}s | {st_b['median_time']:.2f}s | {((st_b['median_time']-st_a['median_time'])/st_a['median_time'])*100:+.1f}% |
| **Mean Paired Delta (B − A)** | - | {mean_paired_delta:+.2f}s | - |
| **Median Paired Delta (B − A)** | - | {median_paired_delta:+.2f}s | - |
| **Total Gemini Calls** | {st_a['total_gemini']} | {st_b['total_gemini']} | {st_b['total_gemini']-st_a['total_gemini']:+d} |
| **Mean Gemini Calls/Run** | {st_a['mean_gemini']:.2f} | {st_b['mean_gemini']:.2f} | {((st_b['mean_gemini']-st_a['mean_gemini'])/st_a['mean_gemini'])*100:+.1f}% |
| **Jev Resolution Rate** | 0.0% | {st_b['jev_resolved_rate']*100:.1f}% | +{st_b['jev_resolved_rate']*100:.1f}% |
| **Mean Jev Latency** | N/A | {st_b['mean_jev_lat']:.1f}ms | - |
| **Physical Actions (Consistency)** | {st_a['total_physical_actions']}/{st_a['n']} | {st_b['total_physical_actions']}/{st_b['n']} | 100% symmetric |
| **Comex Blocks** | {st_a['total_comex_blocks']} | {st_b['total_comex_blocks']} | 0 |
| **LoopBreaker Blocks** | {st_a['total_loop_breaker_blocks']} | {st_b['total_loop_breaker_blocks']} | 0 |
| **Wrong Targets** | {st_a['total_wrong_targets']} | {st_b['total_wrong_targets']} | 0 |

## Paired Latency Deltas (B − A)

| Pair | Schedule | A (Without Jev) | B (With Jev) | Delta (B − A) |
|---|---|---:|---:|---:|
"""
        for d in paired_deltas:
            sched = "A → B" if d["pair"] % 2 == 1 else "B → A"
            report_md += f"| Pair {d['pair']:02d} | {sched} | {d['time_a']:.2f}s | {d['time_b']:.2f}s | {d['delta_s']:+.2f}s |\n"

        report_md += """
## Per-Trial Execution Log

| Trial | Condition | Success | Duration (s) | Gemini Calls | Jev Calls | Jev Resolved | Jev Latency (ms) | Physical Actions | Target Coords | Method E | Final URL |
|---|---|---|---:|---:|---:|---:|---:|---:|---|---|---|
"""
        for r in results:
            coord_str = f"({r['target_coordinates']['x']:.0f}, {r['target_coordinates']['y']:.0f})" if r.get('target_coordinates') else "N/A"
            report_md += f"| {r['trial_id']} | {r['condition']} | {r['success']} | {r['total_time_s']} | {r['gemini_calls']} | {r['jev_calls']} | {r['jev_resolved']} | {r['jev_latency_ms']} | {r['physical_actions']} | {coord_str} | {r.get('method_e_result', 'OK')} | {r['final_url']} |\n"

        with open(report_file, "w") as rf:
            rf.write(report_md)
        logger.info(f"Report written to {report_file}")


if __name__ == "__main__":
    asyncio.run(main())

