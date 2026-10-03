#!/usr/bin/env python3
"""
experiments/run_maps_validation_3runs.py
========================================
Runs 3 identical live Google Maps validation runs with fresh browser state each time.
Captures comprehensive comparative metrics against the 110.04s baseline.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("maps_validation")

ELECTRON_CDP_URL = os.getenv("CDP_URL", "http://127.0.0.1:9222")
AGENT_SERVICE_URL = "http://127.0.0.1:8000"

MAPS_PROMPT = """Open Google Maps and perform fresh research only. Do not use conversation memory or previously stored results.

Find the best way to travel from VIT Chennai to Phoenix Marketcity Chennai using MTC buses only.

Requirements:
- Use only MTC/public buses as the main transport.
- No cab, auto, metro, or train.
- Prefer fewer transfers and shorter total travel time.

Use Google Maps transit directions to determine the route.

Find and report:
1. Starting bus stop near VIT Chennai
2. Bus number(s)
3. Transfer stop(s), if any
4. Final bus stop near Phoenix Marketcity
5. Walking distance at the start and end
6. Approximate total travel time
7. Approximate waiting/transfer time
8. Approximate fare if available
9. Useful operating times if available

Then use Google Maps to find 5 highly rated restaurants inside or near Phoenix Marketcity.

Requirements:
- Rating ≥ 4.2
- At least 500 reviews

For each restaurant report:
- Name
- Rating
- Review count
- Cuisine
- Approximate distance from Phoenix Marketcity

Finally give me:
- The simplest MTC route
- A faster alternative, if one exists
- The 5 restaurant options

Do not book or purchase anything."""


async def reset_browser_tab(cdp_ws: str) -> None:
    """Resets the active browser page to about:blank via CDP."""
    import websockets
    logger.info("Resetting browser tab to about:blank...")
    async with websockets.connect(cdp_ws) as ws:
        msg = {
            "id": 1,
            "method": "Page.navigate",
            "params": {"url": "about:blank"}
        }
        await ws.send(json.dumps(msg))
        await ws.recv()
    await asyncio.sleep(2.0)
    logger.info("Browser tab reset complete.")


def get_active_browser_ws() -> str:
    """Finds the active Electron browser page tab WebSocket."""
    req = urllib.request.urlopen(f"{ELECTRON_CDP_URL}/json/list", timeout=3)
    targets = json.loads(req.read())
    for t in targets:
        if t.get("type") == "page" and "localhost:5173" not in t.get("url", "") and "cursor_overlay" not in t.get("url", ""):
            return t.get("webSocketDebuggerUrl", "")
    raise RuntimeError("No active Electron browser page tab found.")


def run_agent_turn(run_idx: int) -> Dict[str, Any]:
    """Posts the task to agent_service.py /agent/run/stream and tracks progress."""
    task_id = f"maps-val-run-{run_idx}-{int(time.time())}"
    payload = {
        "task": MAPS_PROMPT,
        "taskId": task_id,
        "tabId": "active_tab",
        "url": "about:blank",
        "intent": "visible_browser_task",
        "use_procedures": False,
        "use_graph": False,
    }

    url = f"{AGENT_SERVICE_URL}/agent/run/stream"
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"}
    )

    logger.info(f"\n{'='*70}\nSTARTING RUN {run_idx}: taskId={task_id}\n{'='*70}")
    t0 = time.time()
    final_result_text = ""
    completed_event_data = None
    step_events = []

    with urllib.request.urlopen(req, timeout=300) as resp:
        for line in resp:
            raw_line = line.decode("utf-8").strip()
            if raw_line.startswith("data:"):
                try:
                    data = json.loads(raw_line[5:].strip())
                    event = data.get("event")
                    pdata = data.get("data", {})
                    if event == "step":
                        step_events.append(pdata)
                        tool_name = pdata.get("tool")
                        tool_input = str(pdata.get("input", ""))[:80]
                        logger.info(f"[Run {run_idx}] Step {pdata.get('step')}: {tool_name} -> {tool_input}")
                    elif event == "step_done":
                        logger.info(f"[Run {run_idx}] Step {pdata.get('step')} done: success={pdata.get('success')} in {pdata.get('duration_ms')}ms")
                    elif event == "completed":
                        completed_event_data = pdata
                        final_result_text = pdata.get("result", "") or pdata.get("content", "")
                        logger.info(f"[Run {run_idx}] Completed in {pdata.get('duration_seconds', 0):.2f}s")
                except Exception:
                    pass

    total_wall_clock = time.time() - t0

    # Read latest_timing.json to get authoritative server-side timing metadata
    latest_timing_path = os.path.expanduser("~/.comex/latest_timing.json")
    server_timing = {}
    if os.path.exists(latest_timing_path):
        try:
            with open(latest_timing_path) as f:
                server_timing = json.load(f)
        except Exception:
            pass

    return {
        "run_idx": run_idx,
        "task_id": task_id,
        "wall_clock_time": total_wall_clock,
        "completed_event_data": completed_event_data,
        "server_timing": server_timing,
        "final_result": final_result_text,
    }


def analyze_run_metrics(run_data: Dict[str, Any]) -> Dict[str, Any]:
    """Extracts the 16 exact comparative metrics from run telemetry."""
    st = run_data.get("server_timing", {})
    ts = st.get("timing_summary", {})
    steps = ts.get("steps", [])

    total_time = st.get("duration_seconds", run_data.get("wall_clock_time", 0.0))
    gemini_time = ts.get("total_gemini_duration_s", sum(s.get("llm_duration_s", 0.0) for s in steps))
    tool_time = ts.get("total_tool_duration_s", sum(s.get("tool_duration_s", 0.0) for s in steps))

    harness_calls = sum(1 for s in steps if s.get("tool") == "run_browser_harness")
    action_calls = sum(1 for s in steps if s.get("tool") == "execute_browser_action")
    click_calls = sum(1 for s in steps if s.get("tool") == "browser_click")

    meta = run_data.get("completed_event_data", {}).get("metadata", {}) or {}
    pipeline_telemetries = meta.get("pipeline_telemetries", [])
    tool_traces = meta.get("tool_traces", [])

    jev_time = sum(pt.get("actual_jev_latency_ms", 0.0) for pt in pipeline_telemetries) / 1000.0

    manual_js_scroll_calls = 0
    for tt in tool_traces:
        if tt.get("tool") == "run_browser_harness":
            code = str(tt.get("args", {}).get("code", ""))
            if any(term in code for term in ("scrollBy", "scrollTo", "scrollIntoView", "window.scroll", "scrollTop")):
                manual_js_scroll_calls += 1

    jev_resolved = sum(1 for pt in pipeline_telemetries if pt.get("threshold_passed", False) or pt.get("status") in ("RESOLVED", "SUCCESS"))
    jev_abstained = sum(1 for pt in pipeline_telemetries if "ABSTAINED" in str(pt.get("status", "")))
    occlusion_blocks = sum(1 for pt in pipeline_telemetries if pt.get("status") == "OCCLUDED")
    scroll_attempts = sum(
        pt.get("occlusion_result", {}).get("scroll_attempts", 0) if isinstance(pt.get("occlusion_result"), dict) else 0
        for pt in pipeline_telemetries
    )
    route_card_resolutions = sum(
        1 for pt in pipeline_telemetries
        if "route" in str(pt.get("target_description", "")).lower() or
           "route" in str(pt.get("target_element", "")).lower() or
           "UgZKXd" in str(pt.get("target_class", "")) or
           "trip" in str(pt.get("target_id", "")).lower()
    )
    target_already_selected_blocks = sum(1 for pt in pipeline_telemetries if pt.get("status") == "TARGET_ALREADY_SELECTED")
    physical_actions = sum(1 for s in steps if s.get("tool") in ("execute_browser_action", "browser_click") and s.get("success", True))

    return {
        "total_time": round(total_time, 2),
        "Gemini_time": round(gemini_time, 2),
        "tool_time": round(tool_time, 2),
        "Jev_time": round(jev_time, 2),
        "Gemini_turns": len(steps),
        "run_browser_harness_calls": harness_calls,
        "execute_browser_action_calls": action_calls,
        "manual_JS_scroll_calls": manual_js_scroll_calls,
        "Jev_resolved": jev_resolved,
        "Jev_abstained": jev_abstained,
        "occlusion_blocks": occlusion_blocks,
        "scroll_attempts": scroll_attempts,
        "route_card_resolutions": route_card_resolutions,
        "TARGET_ALREADY_SELECTED_blocks": target_already_selected_blocks,
        "physical_actions": physical_actions,
        "final_success": bool(run_data.get("final_result")),
    }


async def main():
    logger.info("Initializing 3-run live Maps validation...")
    ws_url = get_active_browser_ws()
    logger.info(f"Target Electron page WebSocket: {ws_url}")

    runs_data = []

    for i in range(1, 4):
        logger.info(f"\n>>> PREPARING RUN {i} OF 3 <<<")
        await reset_browser_tab(ws_url)
        run_data = run_agent_turn(i)
        runs_data.append(run_data)
        logger.info(f">>> RUN {i} COMPLETE. Result length: {len(run_data['final_result'])} chars <<<")
        await asyncio.sleep(3.0)

    # Save all run records
    out_path = Path("reports/maps_validation_3runs_results.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(runs_data, f, indent=2)
    logger.info(f"Saved run results to {out_path}")

    # Print summary comparison
    print("\n" + "="*80)
    print("LIVE MAPS VALIDATION EXPERIMENT SUMMARY (3 RUNS)")
    print("="*80)
    print(f"{'Metric':<32} {'Before':<12} {'Run 1':<12} {'Run 2':<12} {'Run 3':<12}")
    print("-" * 80)

    before_metrics = {
        "total_time": "110.04s",
        "Gemini_time": "73.26s",
        "tool_time": "35.02s",
        "Jev_time": "~4.2s",
        "Gemini_turns": "24",
        "run_browser_harness_calls": "16",
        "execute_browser_action_calls": "6",
        "manual_JS_scroll_calls": "3",
        "Jev_resolved": "5",
        "Jev_abstained": "1",
        "occlusion_blocks": "1",
        "scroll_attempts": "0",
        "route_card_resolutions": "0",
        "TARGET_ALREADY_SELECTED_blocks": "0",
        "physical_actions": "7",
        "final_success": "True",
    }

    analyzed = [analyze_run_metrics(r) for r in runs_data]

    for metric, before_val in before_metrics.items():
        r1_val = str(analyzed[0].get(metric, "N/A"))
        r2_val = str(analyzed[1].get(metric, "N/A"))
        r3_val = str(analyzed[2].get(metric, "N/A"))
        if "time" in metric and not r1_val.endswith("s"):
            r1_val += "s"
            r2_val += "s"
            r3_val += "s"
        print(f"{metric:<32} {before_val:<12} {r1_val:<12} {r2_val:<12} {r3_val:<12}")

    print("="*80)


if __name__ == "__main__":
    asyncio.run(main())
