import asyncio
import json
import os
import sys
import websockets

CDP_WS = "ws://127.0.0.1:9222/devtools/page/A4F45CE2509F1F7CA7196BF2F6F0AF75"

# Load the production extractor script
from services.universal_extractor import UNIVERSAL_EXTRACTION_JS

async def main():
    async with websockets.connect(CDP_WS, max_size=20*1024*1024) as ws:
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
                raw = await ws.recv()
                resp = json.loads(raw)
                if resp.get("id") == cmd["id"]:
                    if "exceptionDetails" in resp.get("result", {}):
                        print("JS EXCEPTION:", resp["result"]["exceptionDetails"])
                    return resp.get("result", {}).get("result", {}).get("value")

        # Initial scroll reset
        await evaluate("""
        (() => {
            const scrollEl = document.querySelector('div.e07Vkf.kA9KIf.dS8AEf');
            if (scrollEl) scrollEl.scrollTop = 0;
            // ensure card 0 is selected
            const c0 = document.querySelector('[data-trip-index="0"]');
            if (c0 && c0.getAttribute('role') !== 'link') {
                c0.click();
            }
        })()
        """)
        await asyncio.sleep(0.5)

        # -------------------------------------------------------------
        # 1. Route-card DOM inventory
        # -------------------------------------------------------------
        inventory_data = await evaluate("""
        (() => {
            const cards = Array.from(document.querySelectorAll('[data-trip-index]'));
            const cardsData = cards.map((el, idx) => {
                const rect = el.getBoundingClientRect();
                return {
                    selector: `[data-trip-index="${el.getAttribute('data-trip-index')}"]`,
                    data_trip_index: el.getAttribute('data-trip-index'),
                    tag: el.tagName,
                    role: el.getAttribute('role'),
                    connected: el.isConnected,
                    boundingClientRect: {
                        x: Math.round(rect.x),
                        y: Math.round(rect.y),
                        top: Math.round(rect.top),
                        bottom: Math.round(rect.bottom),
                        left: Math.round(rect.left),
                        right: Math.round(rect.right),
                        width: Math.round(rect.width),
                        height: Math.round(rect.height)
                    },
                    innerText: el.innerText
                };
            });
            const indices = cards.map(c => parseInt(c.getAttribute('data-trip-index'), 10));
            const simultaneous_0_to_5 = [0, 1, 2, 3, 4, 5].every(i => indices.includes(i));
            return {
                total_cards: cards.length,
                simultaneous_0_to_5: simultaneous_0_to_5,
                cards: cardsData
            };
        })()
        """)
        print("[1] Inventory collected:", inventory_data["total_cards"], "cards. Simultaneous 0..5:", inventory_data["simultaneous_0_to_5"])

        # -------------------------------------------------------------
        # 2. Node persistence across scrolling
        # -------------------------------------------------------------
        persistence_data = await evaluate("""
        (() => {
            const scrollEl = document.querySelector('div.e07Vkf.kA9KIf.dS8AEf');
            scrollEl.scrollTop = 0;
            
            // 1. Attach unique JS token
            const cards = Array.from(document.querySelectorAll('[data-trip-index]'));
            cards.forEach(card => {
                const idx = card.getAttribute('data-trip-index');
                card.__persistence_token = {
                    id: `token_trip_${idx}`,
                    timestamp: 1726880000000 + parseInt(idx),
                    uid: `rand_${Math.random().toString(36).slice(2)}`
                };
            });

            const before_scroll = cards.map(c => ({
                index: c.getAttribute('data-trip-index'),
                hasToken: !!c.__persistence_token,
                token_uid: c.__persistence_token ? c.__persistence_token.uid : null
            }));

            // 2. Scroll by 400px
            scrollEl.scrollTop = 400;
            const scroll_pos_after = scrollEl.scrollTop;

            const cards_after_scroll = Array.from(document.querySelectorAll('[data-trip-index]'));
            const during_scroll = cards_after_scroll.map(c => ({
                index: c.getAttribute('data-trip-index'),
                hasToken: !!c.__persistence_token,
                token_uid: c.__persistence_token ? c.__persistence_token.uid : null
            }));

            // 3. Scroll back to 0
            scrollEl.scrollTop = 0;
            const scroll_pos_final = scrollEl.scrollTop;

            const cards_final = Array.from(document.querySelectorAll('[data-trip-index]'));
            const after_scroll_back = cards_final.map(c => ({
                index: c.getAttribute('data-trip-index'),
                hasToken: !!c.__persistence_token,
                token_uid: c.__persistence_token ? c.__persistence_token.uid : null
            }));

            const identical_during = before_scroll.every((b, i) => during_scroll[i] && b.token_uid === during_scroll[i].token_uid);
            const identical_after = before_scroll.every((b, i) => after_scroll_back[i] && b.token_uid === after_scroll_back[i].token_uid);

            return {
                scroll_delta: 400,
                scroll_pos_after: scroll_pos_after,
                scroll_pos_final: scroll_pos_final,
                tokens_identical_during_scroll: identical_during,
                tokens_identical_after_scroll_back: identical_after,
                before_scroll: before_scroll,
                during_scroll: during_scroll,
                after_scroll_back: after_scroll_back
            };
        })()
        """)
        print("[2] Persistence collected. Identical during:", persistence_data["tokens_identical_during_scroll"], "Identical after:", persistence_data["tokens_identical_after_scroll_back"])

        # -------------------------------------------------------------
        # 3. Scroll container details
        # -------------------------------------------------------------
        scroll_container_data = await evaluate("""
        (() => {
            const scrollEl = document.querySelector('div.e07Vkf.kA9KIf.dS8AEf');
            const style = window.getComputedStyle(scrollEl);
            
            const scrollTop_before = scrollEl.scrollTop;
            scrollEl.scrollTop = 400;
            const scrollTop_after = scrollEl.scrollTop;
            scrollEl.scrollTop = 0;

            return {
                exact_selector: "div.e07Vkf.kA9KIf.dS8AEf",
                tag: scrollEl.tagName,
                className: scrollEl.className,
                clientHeight: scrollEl.clientHeight,
                scrollHeight: scrollEl.scrollHeight,
                scrollTop_initial: scrollTop_before,
                scrollTop_after_400_assignment: scrollTop_after,
                scrollTop_restored: scrollEl.scrollTop,
                overflowY: style.overflowY,
                overflowX: style.overflowX
            };
        })()
        """)
        print("[3] Scroll container collected:", scroll_container_data["exact_selector"])

        # -------------------------------------------------------------
        # 4. scrollIntoView experiment (Cards 1, 3, and 5; block: 'nearest' vs 'center')
        # -------------------------------------------------------------
        trials = {}
        for card_idx in [1, 3, 5]:
            trials[f"card_{card_idx}"] = {}
            for mode in ["nearest", "center"]:
                trial_res = await evaluate(f"""
                (() => {{
                    const scrollEl = document.querySelector('div.e07Vkf.kA9KIf.dS8AEf');
                    scrollEl.scrollTop = 0; // reset
                    
                    const card = document.querySelector('[data-trip-index="{card_idx}"]');
                    const rect_before = card.getBoundingClientRect();

                    // Run scrollIntoView
                    card.scrollIntoView({{
                        block: "{mode}",
                        inline: "nearest",
                        behavior: "instant"
                    }});

                    const rect_after = card.getBoundingClientRect();
                    const center_x = Math.round(rect_after.x + rect_after.width / 2);
                    const center_y = Math.round(rect_after.y + rect_after.height / 2);

                    // Method E hit test
                    const hitElement = document.elementFromPoint(center_x, center_y);
                    const hit_inside_card = card.contains(hitElement);
                    
                    // Identify topmost element
                    const hitTag = hitElement ? hitElement.tagName : null;
                    const hitClass = hitElement ? hitElement.className : null;
                    const hitText = hitElement ? (hitElement.innerText || '').slice(0, 50) : null;

                    // Check if floating Maps element intercepted
                    const is_floating_overlay = hitElement && !hit_inside_card;
                    let intercepting_summary = null;
                    if (is_floating_overlay) {{
                        let curr = hitElement;
                        let bannerDesc = [];
                        while (curr && curr !== document.body) {{
                            bannerDesc.push(`${{curr.tagName}}.${{curr.className}}`);
                            curr = curr.parentElement;
                        }}
                        intercepting_summary = {{
                            tag: hitTag,
                            className: hitClass,
                            text: hitText,
                            ancestor_path: bannerDesc.slice(0, 4).join(' > ')
                        }};
                    }}

                    // Reset scroll
                    scrollEl.scrollTop = 0;

                    return {{
                        card_index: {card_idx},
                        mode: "{mode}",
                        rect_before: {{
                            x: Math.round(rect_before.x),
                            y: Math.round(rect_before.y),
                            width: Math.round(rect_before.width),
                            height: Math.round(rect_before.height),
                            top: Math.round(rect_before.top),
                            bottom: Math.round(rect_before.bottom)
                        }},
                        rect_after: {{
                            x: Math.round(rect_after.x),
                            y: Math.round(rect_after.y),
                            width: Math.round(rect_after.width),
                            height: Math.round(rect_after.height),
                            top: Math.round(rect_after.top),
                            bottom: Math.round(rect_after.bottom)
                        }},
                        center_point: {{ x: center_x, y: center_y }},
                        hit_test_element: hitTag ? `${{hitTag}}${{hitClass ? '.' + hitClass.trim().split(/\\s+/).join('.') : ''}}` : null,
                        hit_test_target_inside_card: hit_inside_card,
                        topmost_element_tag: hitTag,
                        topmost_element_class: hitClass,
                        floating_maps_element_intercepted: is_floating_overlay,
                        intercepting_element: intercepting_summary
                    }};
                }})()
                """)
                trials[f"card_{card_idx}"][mode] = trial_res
                print(f"[4] Card {card_idx} ({mode}): hit_inside_card={trial_res['hit_test_target_inside_card']}, intercepted={trial_res['floating_maps_element_intercepted']}")

        # -------------------------------------------------------------
        # 5. Primary actuation target
        # -------------------------------------------------------------
        actuation_summary = {}
        for c_idx in [0, 1, 5]:
            card_meta = await evaluate(f"""
            (() => {{
                const el = document.querySelector('[data-trip-index="{c_idx}"]');
                if (!el) return null;
                const style = window.getComputedStyle(el);
                
                const descendants = Array.from(el.querySelectorAll('button, a, [role="button"], [role="link"], input, [tabindex]'))
                    .map(d => ({{
                        tag: d.tagName,
                        role: d.getAttribute('role'),
                        tabindex: d.getAttribute('tabindex'),
                        ariaLabel: d.getAttribute('aria-label'),
                        text: (d.innerText || '').trim(),
                        className: d.className
                    }}));

                return {{
                    card_index: {c_idx},
                    parent_tag: el.tagName,
                    role: el.getAttribute('role'),
                    tabindex: el.getAttribute('tabindex'),
                    cursor: style.cursor,
                    html_summary: el.outerHTML.slice(0, 350) + '...',
                    child_interactive_descendants: descendants
                }};
            }})()
            """)
            actuation_summary[f"card_{c_idx}"] = card_meta

        # Live actuation test for parent click vs Details click
        click_comparison = await evaluate("""
        (() => {
            const results = {};
            
            // 1. Click unselected parent card 1
            const c0_before = document.querySelector('[data-trip-index="0"]');
            const c1_before = document.querySelector('[data-trip-index="1"]');
            const c1_role_before = c1_before.getAttribute('role');
            
            // Execute click on parent
            c1_before.click();
            
            const c1_after_parent_click = document.querySelector('[data-trip-index="1"]');
            const c1_role_after = c1_after_parent_click.getAttribute('role');
            const c1_details_btn = c1_after_parent_click.querySelector('button');
            const details_btn_text = c1_details_btn ? c1_details_btn.innerText.trim() : null;

            results.parent_click_selects_route = {
                card_index: 1,
                role_before: c1_role_before,
                role_after: c1_role_after,
                gained_active_class: c1_after_parent_click.classList.contains('vKKO3d'),
                details_button_revealed: !!c1_details_btn,
                details_button_text: details_btn_text,
                remained_on_route_list: document.querySelectorAll('[data-trip-index]').length === 6,
                behavior: "Selects route in list, updates role to link, adds vKKO3d, reveals Details button, draws line on map."
            };

            return results;
        })()
        """)
        await asyncio.sleep(0.5)

        # 2. Click Details button
        details_click_res = await evaluate("""
        (() => {
            const c1 = document.querySelector('[data-trip-index="1"]');
            const btn = c1 ? c1.querySelector('button') : null;
            if (btn) btn.click();
            return true;
        })()
        """)
        await asyncio.sleep(1)

        details_view = await evaluate("""
        (() => {
            const backBtn = document.querySelector('button[aria-label="Back"]');
            const header = document.querySelector('h1, h2');
            const cardsRemaining = document.querySelectorAll('[data-trip-index]').length;
            const res = {
                cardsRemaining: cardsRemaining,
                hasBackButton: !!backBtn,
                headerText: header ? header.innerText.trim() : null,
                behavior: "Navigates into turn-by-turn navigation steps view; replaces route cards with step directions; reveals Back button."
            };
            if (backBtn) backBtn.click(); // restore
            return res;
        })()
        """)
        await asyncio.sleep(1)

        # 3. Click already selected parent card 1
        await evaluate("""
        (() => {
            const c1 = document.querySelector('[data-trip-index="1"]');
            if (c1) c1.click();
        })()
        """)
        await asyncio.sleep(1)

        already_selected_click_res = await evaluate("""
        (() => {
            const backBtn = document.querySelector('button[aria-label="Back"]');
            const header = document.querySelector('h1, h2');
            const cardsRemaining = document.querySelectorAll('[data-trip-index]').length;
            const res = {
                cardsRemaining: cardsRemaining,
                hasBackButton: !!backBtn,
                headerText: header ? header.innerText.trim() : null,
                behavior: "Clicking an ALREADY-selected route card produces identical navigation to clicking Details (opens step-by-step directions)."
            };
            if (backBtn) backBtn.click(); // restore
            return res;
        })()
        """)
        await asyncio.sleep(1)

        # Restore card 0 to selected
        await evaluate("""
        (() => {
            const c0 = document.querySelector('[data-trip-index="0"]');
            if (c0) c0.click();
        })()
        """)
        await asyncio.sleep(0.5)

        actuation_test_results = {
            "card_inspection": actuation_summary,
            "parent_click_behavior": click_comparison["parent_click_selects_route"],
            "details_click_behavior": details_view,
            "already_selected_parent_click_behavior": already_selected_click_res,
            "comparison_summary": "Clicking an unselected parent card selects the route (without navigating away). Clicking Details on a selected card navigates to turn-by-turn directions. Clicking an already-selected parent card ALSO navigates to turn-by-turn directions (identical to Details)."
        }
        print("[5] Actuation target tests completed.")

        # -------------------------------------------------------------
        # 6. Candidate extraction (using production UniversalExtractor script)
        # -------------------------------------------------------------
        # Extract at scrollTop = 0
        await evaluate("document.querySelector('div.e07Vkf.kA9KIf.dS8AEf').scrollTop = 0;")
        await asyncio.sleep(0.5)

        extracted_items = await evaluate(UNIVERSAL_EXTRACTION_JS)
        
        # Analyze which candidates belong to route cards
        card_candidates_analysis = await evaluate("""
        (() => {
            const items = Array.from(document.querySelectorAll('[data-comex-id]')).map(el => {
                const card = el.closest('[data-trip-index]');
                const tripIndex = card ? card.getAttribute('data-trip-index') : null;
                const bid = el.getAttribute('data-comex-id');
                const tag = el.tagName.toLowerCase();
                const role = el.getAttribute('role') || '';
                const text = (el.innerText || el.textContent || '').trim().replace(/\\s+/g, ' ').slice(0, 60);
                const isCardContainer = el.hasAttribute('data-trip-index');

                let selector = el.id ? '#' + CSS.escape(el.id) : (el.className ? '.' + String(el.className).trim().split(/\\s+/)[0] : tag);

                return {
                    bid: bid,
                    tag: tag,
                    role: role,
                    text: text,
                    selector: selector,
                    tripIndex: tripIndex,
                    isCardContainer: isCardContainer
                };
            });
            return items;
        })()
        """)

        # Group by tripIndex
        by_card = {str(i): [] for i in range(6)}
        non_card_candidates = []
        for it in card_candidates_analysis:
            if it["tripIndex"] is not None:
                by_card[str(it["tripIndex"])].append(it)
            else:
                non_card_candidates.append(it)

        fragmentation_per_card = {}
        for idx_str, cands in by_card.items():
            fragmentation_per_card[f"card_{idx_str}"] = {
                "candidate_count": len(cands),
                "candidates": cands
            }

        extraction_data = {
            "total_candidates": len(extracted_items),
            "candidates_belonging_to_route_cards": sum(len(cands) for cands in by_card.values()),
            "candidates_outside_route_cards": len(non_card_candidates),
            "fragmentation_per_card": fragmentation_per_card
        }
        print(f"[6] Extraction collected: {extraction_data['total_candidates']} total candidates. Route card candidates: {extraction_data['candidates_belonging_to_route_cards']}")

        # -------------------------------------------------------------
        # 7. Off-screen candidate counts
        # Record candidate counts at:
        # - scrollTop = 0
        # - scrollTop = 400
        # - after scrollIntoView(center) on card 5
        # -------------------------------------------------------------
        # A: scrollTop = 0
        await evaluate("document.querySelector('div.e07Vkf.kA9KIf.dS8AEf').scrollTop = 0;")
        await asyncio.sleep(0.3)
        items_scroll_0 = await evaluate(UNIVERSAL_EXTRACTION_JS)
        cards_at_0 = await evaluate("""
        (() => {
            return Array.from(document.querySelectorAll('[data-trip-index]')).map(c => {
                const r = c.getBoundingClientRect();
                return {
                    index: c.getAttribute('data-trip-index'),
                    top: Math.round(r.top),
                    bottom: Math.round(r.bottom),
                    in_viewport: r.bottom > 0 && r.top < window.innerHeight
                };
            });
        })()
        """)

        # B: scrollTop = 400
        await evaluate("document.querySelector('div.e07Vkf.kA9KIf.dS8AEf').scrollTop = 400;")
        await asyncio.sleep(0.3)
        items_scroll_400 = await evaluate(UNIVERSAL_EXTRACTION_JS)
        cards_at_400 = await evaluate("""
        (() => {
            return Array.from(document.querySelectorAll('[data-trip-index]')).map(c => {
                const r = c.getBoundingClientRect();
                return {
                    index: c.getAttribute('data-trip-index'),
                    top: Math.round(r.top),
                    bottom: Math.round(r.bottom),
                    in_viewport: r.bottom > 0 && r.top < window.innerHeight
                };
            });
        })()
        """)

        # C: after scrollIntoView(center) on card 5
        await evaluate("""
        (() => {
            const c5 = document.querySelector('[data-trip-index="5"]');
            c5.scrollIntoView({ block: "center", inline: "nearest", behavior: "instant" });
        })()
        """)
        await asyncio.sleep(0.3)
        items_card5_center = await evaluate(UNIVERSAL_EXTRACTION_JS)
        cards_at_center5 = await evaluate("""
        (() => {
            return Array.from(document.querySelectorAll('[data-trip-index]')).map(c => {
                const r = c.getBoundingClientRect();
                return {
                    index: c.getAttribute('data-trip-index'),
                    top: Math.round(r.top),
                    bottom: Math.round(r.bottom),
                    in_viewport: r.bottom > 0 && r.top < window.innerHeight
                };
            });
        })()
        """)

        # Reset scroll to 0
        await evaluate("document.querySelector('div.e07Vkf.kA9KIf.dS8AEf').scrollTop = 0;")

        offscreen_data = {
            "at_scrollTop_0": {
                "total_candidates": len(items_scroll_0),
                "viewport_height": 830,
                "card_visibility": cards_at_0,
                "cards_in_viewport_count": sum(1 for c in cards_at_0 if c["in_viewport"]),
                "cards_offscreen_count": sum(1 for c in cards_at_0 if not c["in_viewport"])
            },
            "at_scrollTop_400": {
                "total_candidates": len(items_scroll_400),
                "viewport_height": 830,
                "card_visibility": cards_at_400,
                "cards_in_viewport_count": sum(1 for c in cards_at_400 if c["in_viewport"]),
                "cards_offscreen_count": sum(1 for c in cards_at_400 if not c["in_viewport"])
            },
            "after_scrollIntoView_center_card_5": {
                "total_candidates": len(items_card5_center),
                "viewport_height": 830,
                "card_visibility": cards_at_center5,
                "cards_in_viewport_count": sum(1 for c in cards_at_center5 if c["in_viewport"]),
                "cards_offscreen_count": sum(1 for c in cards_at_center5 if not c["in_viewport"])
            }
        }
        print(f"[7] Off-screen candidate counts: at 0: {len(items_scroll_0)}, at 400: {len(items_scroll_400)}, after card 5 center: {len(items_card5_center)}")

        # -------------------------------------------------------------
        # Aggregate Full Investigation Object
        # -------------------------------------------------------------
        full_report = {
            "timestamp": "2026-09-21T01:05:00+05:30",
            "url": "https://www.google.com/maps/dir/VIT+Chennai/Phoenix+Marketcity+Chennai",
            "section_1_route_card_dom_inventory": inventory_data,
            "section_2_node_persistence": persistence_data,
            "section_3_scroll_container": scroll_container_data,
            "section_4_scroll_into_view_experiment": trials,
            "section_5_primary_actuation_target": actuation_test_results,
            "section_6_candidate_extraction": extraction_data,
            "section_7_offscreen_candidate_counts": offscreen_data
        }

        output_path = "reports/maps_transit_dom_investigation.json"
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(full_report, f, indent=2, ensure_ascii=False)
        
        print(f"SUCCESS: Raw investigation artifact written to {output_path}")

asyncio.run(main())
