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
            const card0 = document.querySelector('[data-trip-index="0"]');
            let el = card0;
            let scrollEl = null;
            while (el && el !== document.body) {
                const style = window.getComputedStyle(el);
                if (style.overflowY === 'auto' || style.overflowY === 'scroll') {
                    scrollEl = el;
                    break;
                }
                el = el.parentElement;
            }
            if (!scrollEl) return null;
            
            // build selector
            function getPath(node) {
                if (node.id) return '#' + CSS.escape(node.id);
                if (node.getAttribute('role')) return `${node.tagName.toLowerCase()}[role="${node.getAttribute('role')}"]`;
                let sel = node.tagName.toLowerCase();
                if (node.className && typeof node.className === 'string') {
                    const cls = node.className.trim().split(/\\s+/).join('.');
                    if (cls) sel += '.' + cls;
                }
                return sel;
            }
            
            let curr = scrollEl;
            const path = [];
            while (curr && curr !== document.body && curr !== document.documentElement) {
                path.unshift(getPath(curr));
                curr = curr.parentElement;
            }

            return {
                exactClassSelector: 'div.' + scrollEl.className.trim().split(/\\s+/).join('.'),
                uniqueMatchCount: document.querySelectorAll('div.' + scrollEl.className.trim().split(/\\s+/).join('.')).length,
                fullPath: path.join(' > '),
                ariaLabel: scrollEl.getAttribute('aria-label'),
                role: scrollEl.getAttribute('role')
            };
        })()
        """
        res = await evaluate(js)
        print("Scroll container selector:", json.dumps(res, indent=2))

asyncio.run(main())
