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

        # Check state before clicking card 1
        s0 = await evaluate("""
        (() => {
            const c0 = document.querySelector('[data-trip-index="0"]');
            const c1 = document.querySelector('[data-trip-index="1"]');
            return {
                c0_class: c0.className,
                c0_role: c0.getAttribute('role'),
                c0_hasDetails: !!c0.querySelector('button'),
                c1_class: c1.className,
                c1_role: c1.getAttribute('role'),
                c1_hasDetails: !!c1.querySelector('button'),
            };
        })()
        """)
        print("Before click card 1:", s0)

        # Click card 1
        s1 = await evaluate("""
        (() => {
            const c1 = document.querySelector('[data-trip-index="1"]');
            c1.click();
            return true;
        })()
        """)
        await asyncio.sleep(1)

        # Check state after clicking card 1
        s2 = await evaluate("""
        (() => {
            const c0 = document.querySelector('[data-trip-index="0"]');
            const c1 = document.querySelector('[data-trip-index="1"]');
            return {
                c0_class: c0 ? c0.className : null,
                c0_role: c0 ? c0.getAttribute('role') : null,
                c0_hasDetails: !!(c0 && c0.querySelector('button')),
                c1_class: c1 ? c1.className : null,
                c1_role: c1 ? c1.getAttribute('role') : null,
                c1_hasDetails: !!(c1 && c1.querySelector('button')),
                detailsButtonText: c1 && c1.querySelector('button') ? c1.querySelector('button').innerText : null
            };
        })()
        """)
        print("After click card 1:", s2)

        # Restore: click card 0 back
        await evaluate("""
        (() => {
            const c0 = document.querySelector('[data-trip-index="0"]');
            if (c0) c0.click();
        })()
        """)
        await asyncio.sleep(0.5)

asyncio.run(main())
