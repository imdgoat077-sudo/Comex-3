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

        # Check initial route cards
        res0 = await evaluate("""
        (() => {
            const cards = Array.from(document.querySelectorAll('[data-trip-index]'));
            return cards.map(c => ({
                index: c.getAttribute('data-trip-index'),
                role: c.getAttribute('role'),
                classes: c.className,
                hasButton: !!c.querySelector('button'),
                buttonText: c.querySelector('button') ? c.querySelector('button').innerText : null
            }));
        })()
        """)
        print("Route cards initial state:", json.dumps(res0, indent=2))

        # Click unselected card 2
        print("Clicking unselected card 2...")
        await evaluate("document.querySelector('[data-trip-index=\"2\"]').click()")
        await asyncio.sleep(1)

        res1 = await evaluate("""
        (() => {
            const cards = Array.from(document.querySelectorAll('[data-trip-index]'));
            return {
                cardCount: cards.length,
                cards: cards.map(c => ({
                    index: c.getAttribute('data-trip-index'),
                    role: c.getAttribute('role'),
                    classes: c.className,
                    hasButton: !!c.querySelector('button'),
                    buttonText: c.querySelector('button') ? c.querySelector('button').innerText : null
                }))
            };
        })()
        """)
        print("State after clicking unselected card 2:", json.dumps(res1, indent=2))

        # Now click Details button inside card 2
        print("Clicking Details button inside selected card 2...")
        await evaluate("document.querySelector('[data-trip-index=\"2\"] button').click()")
        await asyncio.sleep(1)

        res2 = await evaluate("""
        (() => {
            const backBtn = document.querySelector('button[aria-label="Back"]');
            return {
                cardCount: document.querySelectorAll('[data-trip-index]').length,
                hasBackButton: !!backBtn,
                header: document.querySelector('h1, h2') ? document.querySelector('h1, h2').innerText : null
            };
        })()
        """)
        print("State after clicking Details button:", json.dumps(res2, indent=2))

        # Restore: click Back
        if res2["hasBackButton"]:
            print("Restoring via Back button...")
            await evaluate("document.querySelector('button[aria-label=\"Back\"]').click()")
            await asyncio.sleep(1)

        # What if we click an already selected card?
        print("Testing click on an already selected card (card 2)...")
        # Ensure card 2 is selected
        await evaluate("document.querySelector('[data-trip-index=\"2\"]').click()")
        await asyncio.sleep(0.5)
        # Click it again
        await evaluate("document.querySelector('[data-trip-index=\"2\"]').click()")
        await asyncio.sleep(1)
        res3 = await evaluate("""
        (() => {
            const backBtn = document.querySelector('button[aria-label="Back"]');
            return {
                cardCount: document.querySelectorAll('[data-trip-index]').length,
                hasBackButton: !!backBtn,
                header: document.querySelector('h1, h2') ? document.querySelector('h1, h2').innerText : null
            };
        })()
        """)
        print("State after clicking already selected card 2:", json.dumps(res3, indent=2))

        if res3["hasBackButton"]:
            print("Restoring via Back button...")
            await evaluate("document.querySelector('button[aria-label=\"Back\"]').click()")
            await asyncio.sleep(1)

        # Restore card 0 to selected
        await evaluate("document.querySelector('[data-trip-index=\"0\"]').click()")
        await asyncio.sleep(0.5)

asyncio.run(main())
