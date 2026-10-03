"""
scripts/run_phase_b_smoke.py
============================
Phase B Minimal Live-Web Smoke Test (WAN 2500ms Timeout Follow-up).

Executes a single diagnostic interaction on a lightweight public surface (Hacker News)
through the production pipeline in agent_service.py to evaluate live Jev resolution:
    agent_service.py (execute_agent_loop)
        ↓
    Navigate to https://news.ycombinator.com
        ↓
    Universal Candidate Extraction
        ↓
    Gemini → Jev resolution (MicroActionResolver, 2500ms WAN budget)
        ↓
    Method E / ActuatorGate
        ↓
    BrowserActuator physical dispatch
        ↓
    Post-action fingerprint + LoopBreaker
        ↓
    Action ledger / execution trace output
"""

import asyncio
import json
import logging
import os
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("phase_b_smoke")

from agent_service import execute_agent_loop, CDP_URL


async def run_smoke():
    logger.info("Starting Phase B Live-Web Smoke Test (WAN 2500ms follow-up)...")

    # 1. Discover active page target in Electron
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
    logger.info(f"Using target {target_id}: {active_target.get('url')} -> WS: {target_ws}")

    # 2. Reset foreground tab to https://news.ycombinator.com
    import websockets
    async with websockets.connect(target_ws, max_size=20 * 1024 * 1024) as ws:
        logger.info("Navigating tab to https://news.ycombinator.com...")
        nav_msg = {
            "id": 1001,
            "method": "Page.navigate",
            "params": {"url": "https://news.ycombinator.com"}
        }
        await ws.send(json.dumps(nav_msg))
        await ws.recv()

    # Wait for page to settle
    await asyncio.sleep(2.5)

    # Capture initial pre_url
    async with websockets.connect(target_ws, max_size=20 * 1024 * 1024) as ws:
        eval_msg = {
            "id": 1002,
            "method": "Runtime.evaluate",
            "params": {"expression": "window.location.href", "returnByValue": True}
        }
        await ws.send(json.dumps(eval_msg))
        raw = await ws.recv()
        res = json.loads(raw)
        pre_url = res.get("result", {}).get("result", {}).get("value", "unknown")

    # 3. Track events emitted by agent loop
    events = []
    def on_event(event_type, data):
        events.append({"type": event_type, "data": data, "timestamp": time.monotonic()})
        logger.info(f"[AGENT EVENT: {event_type}] {data}")

    task = "Click on the 'new' link in the top navigation bar to view newest submissions."
    logger.info(f"Submitting task to execute_agent_loop: '{task}'")

    t_start = time.monotonic()
    result = await execute_agent_loop(
        task=task,
        default_cdp_url=target_ws,
        intent="visible_browser_task",
        use_procedures=False,
        use_graph=True,
        event_callback=on_event
    )
    t_elapsed = time.monotonic() - t_start

    # Capture post_url
    async with websockets.connect(target_ws, max_size=20 * 1024 * 1024) as ws:
        eval_msg = {
            "id": 1003,
            "method": "Runtime.evaluate",
            "params": {"expression": "window.location.href", "returnByValue": True}
        }
        await ws.send(json.dumps(eval_msg))
        raw = await ws.recv()
        res = json.loads(raw)
        post_url = res.get("result", {}).get("result", {}).get("value", "unknown")

    metadata = result.metadata if hasattr(result, "metadata") else {}
    telemetry = metadata.get("pipeline_telemetry", {})
    tool_traces = metadata.get("tool_traces", [])

    used_fallback = any(t.get("tool") != "execute_browser_action" for t in tool_traces)

    # 4. Print Structured Telemetry Report
    print("\n" + "=" * 70)
    print("PHASE B REAL-WEB JEV RESOLUTION TELEMETRY REPORT")
    print("=" * 70)
    print(f"Task: {task}")
    print(f"Elapsed Total Loop Time: {t_elapsed:.2f}s")
    print("-" * 70)
    print(f"1.  Resolver Timeout Configured:    {telemetry.get('configured_timeout_ms', 'N/A')} ms")
    print(f"2.  Actual Jev Latency:             {telemetry.get('actual_jev_latency_ms', 'N/A')} ms")
    jev_status = "RESOLVED" if telemetry.get('status') in ("SUCCESS", "RESOLVED") else ("ABSTAINED" if telemetry.get('status') == "ABSTAINED" else telemetry.get('status', 'N/A'))
    print(f"3.  Jev Status:                     {jev_status}")
    print(f"4.  Abstain Reason:                 {telemetry.get('abstention_reason') or 'None'}")
    print(f"5.  Target Selector:                {telemetry.get('target_selector', 'N/A')}")
    print(f"6.  Target Class:                   {telemetry.get('target_class', 'N/A')}")
    print(f"7.  Target Text / Accessible Name:  {telemetry.get('target_text', 'N/A')}")
    print(f"8.  Confidence:                     {telemetry.get('confidence', 'N/A')}")
    print(f"9.  Margin:                         {telemetry.get('margin', 'N/A')}")
    print(f"10. Threshold Conditions Passed:    {telemetry.get('threshold_passed', False)}")
    print(f"11. Comex Decision:                 {'ALLOW' if telemetry.get('threshold_passed') else 'BLOCKED'}")
    print(f"12. Method E / Occlusion Result:    {telemetry.get('occlusion_result', 'N/A')}")
    print(f"13. Physical Dispatch Result:       {telemetry.get('execution_result', 'N/A')}")
    print(f"14. Pre URL:                        {pre_url}")
    print(f"    Post URL:                       {post_url}")
    print(f"15. Pre Fingerprint:                {telemetry.get('pre_fingerprint', 'N/A')[:16]}...")
    print(f"    Post Fingerprint:               {telemetry.get('post_fingerprint', 'N/A')[:16]}...")
    print(f"16. State Changed:                  {telemetry.get('state_changed', 'N/A')}")
    print(f"17. Gemini Fallback Used:           {used_fallback}")
    print("-" * 70)
    print(f"Agent Final Output: {str(result)}")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(run_smoke())
