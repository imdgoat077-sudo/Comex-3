import requests
import json
import time

url = "http://127.0.0.1:8000/agent/run"
payload = {
    "task": "Open YouTube and search for Lo-Fi Beats.",
    "tabId": "A4F45CE2509F1F7CA7196BF2F6F0AF75",
    "taskId": "smoke_youtube_search_lofi",
    "intent": "visible_browser_task"
}

print(f"[{time.strftime('%X')}] Sending task: {payload['task']}")
t0 = time.perf_counter()
try:
    resp = requests.post(url, json=payload, timeout=180)
    dur = time.perf_counter() - t0
    print(f"[{time.strftime('%X')}] Task completed in {dur:.2f}s (Status: {resp.status_code})")
    data = resp.json()
    print("\n--- AGENT RESULT ---")
    print(data.get("result"))

    meta = data.get("metadata", {})
    pipeline_runs = meta.get("pipeline_telemetries", [])
    if not pipeline_runs and "pipeline_telemetry" in meta:
        pipeline_runs = [meta["pipeline_telemetry"]]

    print(f"\n--- MICRO-ACTION PIPELINE TELEMETRY ({len(pipeline_runs)} actions) ---")
    for idx, p in enumerate(pipeline_runs, 1):
        print(f"\n[Action #{idx}: {p.get('operation')} on '{p.get('target_description')}']")
        print(f"  Status: {p.get('status')}")
        print(f"  Target ID: {p.get('target_id')} (BID: {p.get('target_bid')})")
        print(f"  Target Element: {p.get('target_element')}")
        print(f"  Target Selector: {p.get('target_selector')}")
        print(f"  Jev Confidence: {p.get('confidence')} (Threshold Passed: {p.get('threshold_passed')})")
        print(f"  Jev Margin: {p.get('margin')}")
        print(f"  Jev Latency: {p.get('actual_jev_latency_ms'):.2f}ms")
        print(f"  Total Duration: {p.get('duration_ms'):.2f}ms")
        print(f"  Physical Actuation: {p.get('execution_result')}")
        print(f"  State Changed: {p.get('state_changed')}")
        if p.get('abstention_reason'):
            print(f"  Abstention: {p.get('abstention_reason')}")

    steps = meta.get("timing", {}).get("steps", [])
    print(f"\n--- STEP BREAKDOWN ({len(steps)} steps) ---")
    for s in steps:
        tool_name = s.get("tool_name", "None")
        print(f"  Step {s.get('step')}: LLM={s.get('llm_duration_s', 0):.2f}s | Tool '{tool_name}'={s.get('tool_duration_s', 0):.2f}s (Success={s.get('success')})")

except Exception as e:
    print(f"Error executing task: {e}")
