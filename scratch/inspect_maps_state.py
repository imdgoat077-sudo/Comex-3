import asyncio
import json
import websockets

CDP_WS = "ws://127.0.0.1:9222/devtools/page/A4F45CE2509F1F7CA7196BF2F6F0AF75"

async def main():
    async with websockets.connect(CDP_WS, max_size=10*1024*1024) as ws:
        msg_id = 1
        
        async def evaluate(expr):
            nonlocal msg_id
            cmd = {
                "id": msg_id,
                "method": "Runtime.evaluate",
                "params": {
                    "expression": expr,
                    "returnByValue": True,
                    "awaitPromise": True
                }
            }
            msg_id += 1
            await ws.send(json.dumps(cmd))
            while True:
                resp = json.loads(await ws.recv())
                if resp.get("id") == cmd["id"]:
                    return resp.get("result", {}).get("result", {}).get("value")

        js_check = """
        (() => {
            const cards = Array.from(document.querySelectorAll('[data-trip-index]'));
            return {
                url: window.location.href,
                cardCount: cards.length,
                indices: cards.map(c => c.getAttribute('data-trip-index')),
                tags: cards.map(c => c.tagName),
                roles: cards.map(c => c.getAttribute('role')),
                tabindexes: cards.map(c => c.getAttribute('tabindex')),
                classes: cards.map(c => c.className)
            };
        })()
        """
        res = await evaluate(js_check)
        print("Initial state:", json.dumps(res, indent=2))

asyncio.run(main())
