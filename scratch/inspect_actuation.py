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

        js = """
        (() => {
            const cards = [0, 1, 5].map(idx => {
                const el = document.querySelector(`[data-trip-index="${idx}"]`);
                if (!el) return { index: idx, found: False };
                const style = window.getComputedStyle(el);
                
                // Find interactive descendants
                const descendants = Array.from(el.querySelectorAll('button, a, [role="button"], [role="link"], input, [tabindex]'))
                    .map(d => ({
                        tag: d.tagName,
                        role: d.getAttribute('role'),
                        tabindex: d.getAttribute('tabindex'),
                        ariaLabel: d.getAttribute('aria-label'),
                        text: (d.innerText || '').trim(),
                        className: d.className
                    }));

                return {
                    index: idx,
                    tag: el.tagName,
                    role: el.getAttribute('role'),
                    tabindex: el.getAttribute('tabindex'),
                    cursor: style.cursor,
                    htmlSummary: el.outerHTML.slice(0, 300) + '...',
                    descendantCount: descendants.length,
                    descendants: descendants
                };
            });
            return cards;
        })()
        """
        res = await evaluate(js)
        print("Actuation inspection:", json.dumps(res, indent=2))

asyncio.run(main())
