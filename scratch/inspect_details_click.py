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

        # First ensure card 0 is selected
        await evaluate("document.querySelector('[data-trip-index=\"0\"]').click()")
        await asyncio.sleep(0.5)

        # Check Details button
        det = await evaluate("""
        (() => {
            const btn = document.querySelector('[data-trip-index="0"] button');
            return {
                exists: !!btn,
                text: btn ? btn.innerText : null,
                ariaLabel: btn ? btn.getAttribute('aria-label') : null,
                outerHTML: btn ? btn.outerHTML : null
            };
        })()
        """)
        print("Details button:", det)

        # Click Details button
        await evaluate("document.querySelector('[data-trip-index=\"0\"] button').click()")
        await asyncio.sleep(1)

        # Check what view we are in
        view_after = await evaluate("""
        (() => {
            const cards = document.querySelectorAll('[data-trip-index]');
            const backBtn = document.querySelector('button[aria-label="Back"]');
            const header = document.querySelector('h1, h2');
            return {
                cardCount: cards.length,
                hasBackButton: !!backBtn,
                backButtonAria: backBtn ? backBtn.getAttribute('aria-label') : null,
                url: window.location.href,
                headerText: header ? header.innerText : null
            };
        })()
        """)
        print("View after Details click:", view_after)

        # Return back to route list!
        if view_after["hasBackButton"]:
            print("Clicking back button to restore route list...")
            await evaluate("document.querySelector('button[aria-label=\"Back\"]').click()")
            await asyncio.sleep(1)

        # Verify restoration
        view_restored = await evaluate("""
        (() => {
            return {
                cardCount: document.querySelectorAll('[data-trip-index]').length,
                url: window.location.href
            };
        })()
        """)
        print("Restored view:", view_restored)

asyncio.run(main())
