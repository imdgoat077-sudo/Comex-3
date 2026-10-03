"""
Unit tests for production BrowserActuator service.
"""

import asyncio
import json
import pytest
from services.browser_actuator import ActuationResult, ActuationStatus, BrowserActuator


class MockCDPWebSocket:
    """Mock CDP WebSocket connection simulating browser responses."""

    def __init__(self, response_handlers=None):
        self.sent_messages = []
        self.response_handlers = response_handlers or {}

    async def send(self, message: str) -> None:
        self.sent_messages.append(json.loads(message))

    async def recv(self) -> str:
        last = self.sent_messages[-1]
        msg_id = last["id"]
        method = last.get("method")
        expr = last.get("params", {}).get("expression", "")

        val = None
        for pattern, handler in self.response_handlers.items():
            if pattern in expr:
                val = handler(last)
                break

        return json.dumps({
            "id": msg_id,
            "result": {"result": {"value": val}}
        })


@pytest.mark.asyncio
async def test_validate_occlusion_success():
    actuator = BrowserActuator()
    ws = MockCDPWebSocket(response_handlers={
        "document.elementFromPoint": lambda msg: {"status": "OK", "x": 120, "y": 250}
    })
    msg_counter = [1]
    res = await actuator.validate_occlusion(ws, bid="1", selector=None, msg_counter=msg_counter)
    assert res["status"] == "OK"
    assert res["x"] == 120
    assert res["y"] == 250


@pytest.mark.asyncio
async def test_validate_occlusion_blocked():
    actuator = BrowserActuator()
    ws = MockCDPWebSocket(response_handlers={
        "document.elementFromPoint": lambda msg: {
            "status": "STALE_TARGET_OCCLUDED",
            "x": 120,
            "y": 250,
            "top_tag": "DIV",
            "top_class": "modal-backdrop"
        }
    })
    msg_counter = [1]
    res = await actuator.validate_occlusion(ws, bid="1", selector=None, msg_counter=msg_counter)
    assert res["status"] == "STALE_TARGET_OCCLUDED"
    assert res["top_tag"] == "DIV"


@pytest.mark.asyncio
async def test_execute_click_aborts_on_occlusion():
    actuator = BrowserActuator()
    # Occlusion check fails
    ws = MockCDPWebSocket(response_handlers={
        "document.elementFromPoint": lambda msg: {
            "status": "STALE_TARGET_OCCLUDED",
            "x": 100,
            "y": 100
        }
    })
    msg_counter = [1]
    res = await actuator.execute_click(ws, bid="1", selector=None, msg_counter=msg_counter)

    assert res.is_success is False
    assert res.was_occluded is True
    assert res.status == ActuationStatus.STALE_TARGET_OCCLUDED
    # Verify that physical click was NOT sent
    assert not any("el.click()" in m.get("params", {}).get("expression", "") for m in ws.sent_messages)


@pytest.mark.asyncio
async def test_execute_click_succeeds_when_clear():
    actuator = BrowserActuator()
    ws = MockCDPWebSocket(response_handlers={
        "document.elementFromPoint": lambda msg: {"status": "OK", "x": 150, "y": 300},
        "el.click()": lambda msg: True
    })
    msg_counter = [1]
    res = await actuator.execute_click(ws, bid="1", selector=None, msg_counter=msg_counter)

    assert res.is_success is True
    assert res.status == ActuationStatus.SUCCESS
    assert res.x == 150.0
    assert res.y == 300.0


@pytest.mark.asyncio
async def test_execute_atomic_fill():
    actuator = BrowserActuator()
    ws = MockCDPWebSocket(response_handlers={
        "document.elementFromPoint": lambda msg: {"status": "OK", "x": 100, "y": 200},
        "prototypeValueSetter": lambda msg: "OK"
    })
    msg_counter = [1]
    res = await actuator.execute_atomic_fill(ws, bid="2", selector=None, text="Zurich", msg_counter=msg_counter)

    assert res.is_success is True
    assert res.action == "fill"


@pytest.mark.asyncio
async def test_visual_cursor_non_blocking_fire_and_forget():
    import time
    # Target an unreachable port: synchronous HTTP would take 200ms+ or timeout
    actuator = BrowserActuator(cursor_notify_url="http://127.0.0.1:59999/dummy")
    t0 = time.perf_counter()
    await actuator.notify_visual_cursor(x=200, y=400, action="click")
    duration_ms = (time.perf_counter() - t0) * 1000.0

    # Must complete almost instantaneously (under 15ms)
    assert duration_ms < 15.0, f"notify_visual_cursor blocked the event loop! Duration: {duration_ms}ms"

