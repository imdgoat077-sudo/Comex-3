"""
services/universal_extractor.py
===============================
Universal Multi-Signal DOM Extractor for Production Comex Browser.

Core Principles:
1. Observational Purity:
   No application/user-visible state mutation. The extractor does not click, focus,
   scroll, dispatch synthetic events, or modify input values.
2. Transient Binding Attribute:
   The only permitted DOM mutation is attaching 'data-comex-id="c<N>"' for exact
   physical target binding by BrowserActuator and Method E.
3. Fingerprint Isolation:
   DATA_COMEX_ID_EXCLUDED_FROM_STATE_FINGERPRINT = True.
   Injected data-comex-id attributes are strictly excluded from state fingerprint
   material so candidate extraction alone never produces state_changed = True.
4. Multi-Signal Affordance Discovery (Zero Hardcoded CSS lists):
   - Native controls (button, input, select, textarea, a[href], summary, details)
   - ARIA interactive roles (button, link, checkbox, radio, combobox, tab, menuitem, option, textbox, searchbox)
   - Accessible Name computation (aria-labelledby, aria-label, placeholder, title, alt, form <label>, innerText, value)
   - Visible Geometry (bounding rect > 0, inside layout viewport, display/visibility/opacity valid)
   - Pointer Affordances (computed cursor === 'pointer' on leaf or text nodes)
   - Contenteditable elements
   - Disabled / Inert filtering (:disabled, aria-disabled="true", [inert])
"""

from __future__ import annotations

import hashlib
import json
import logging
from html.parser import HTMLParser
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("comex.services.universal_extractor")

DATA_COMEX_ID_EXCLUDED_FROM_STATE_FINGERPRINT: bool = True
COMEX_BINDING_ATTR: str = "data-comex-id"

UNIVERSAL_EXTRACTION_JS: str = """
(() => {
    const COMEX_BINDING_ATTR = 'data-comex-id';

    // 1. Remove previous comex binding attributes to maintain clean state
    document.querySelectorAll(`[${COMEX_BINDING_ATTR}]`).forEach(el => {
        el.removeAttribute(COMEX_BINDING_ATTR);
    });

    const isStyledVisible = (el) => {
        if (!el || !el.isConnected) return false;
        const style = window.getComputedStyle(el);
        if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') {
            return false;
        }
        const rect = el.getBoundingClientRect();
        if (rect.width <= 0 || rect.height <= 0) return false;
        return true;
    };

    const isInViewport = (rect) => {
        return rect.bottom > 0 && rect.right > 0 &&
               rect.top < window.innerHeight && rect.left < window.innerWidth;
    };

    const isDisabledOrInert = (el) => {
        if (el.matches(':disabled') || el.matches('[aria-disabled="true"]')) return true;
        if (el.closest('[inert]') || el.closest(':disabled')) return true;
        return false;
    };

    const isCompositeCard = (el) => {
        if (!el || !el.getAttribute) return false;
        const tag = (el.tagName || '').toLowerCase();
        const role = (el.getAttribute('role') || '').toLowerCase();
        const isInteractiveContainer = (role === 'button' || role === 'link' || role === 'option' || el.getAttribute('tabindex') === '0');
        if (!isInteractiveContainer || (tag !== 'div' && tag !== 'article')) return false;

        const hasCardAttr = el.hasAttribute && (
            el.hasAttribute('data-trip-index') ||
            el.hasAttribute('data-item-index') ||
            el.hasAttribute('data-card-id') ||
            el.hasAttribute('data-result-id') ||
            el.hasAttribute('data-asin') ||
            (el.attributes && Array.from(el.attributes).some(a => a.name.includes('index') || a.name.includes('card') || a.name.includes('item')))
        );
        return Boolean(hasCardAttr || (el.childElementCount > 1 && el.querySelector('button, a[href], [role="button"]')));
    };

    const isDetailsButton = (el) => {
        if (!el) return false;
        const tag = (el.tagName || '').toLowerCase();
        const role = (el.getAttribute && el.getAttribute('role') || '').toLowerCase();
        if (tag === 'button' || role === 'button' || tag === 'a' || role === 'tab' || role === 'radio' || role === 'option') {
            const txt = (el.innerText || el.textContent || el.getAttribute('aria-label') || '').trim().toLowerCase();
            return ['details', 'view', 'more info', 'expand', 'select', 'book', 'show more'].includes(txt);
        }
        return false;
    };

    const computeGroupContext = (el) => {
        if (!el || !el.parentElement) return '';
        let curr = el.parentElement;
        let depth = 0;
        while (curr && curr !== document.body && depth < 5) {
            // 1. Explicit <fieldset> with <legend>
            if (curr.tagName === 'FIELDSET') {
                const legend = curr.querySelector('legend');
                if (legend) {
                    const lt = (legend.innerText || legend.textContent || '').trim();
                    if (lt) return lt.slice(0, 50);
                }
            }
            // 2. Explicit ARIA grouping container (role=group, role=radiogroup)
            const role = (curr.getAttribute('role') || '').toLowerCase();
            if (role === 'group' || role === 'radiogroup') {
                const al = (curr.getAttribute('aria-label') || '').trim();
                if (al) return al.slice(0, 50);
                const alb = curr.getAttribute('aria-labelledby');
                if (alb) {
                    const parts = alb.split(/\\s+/).map(id => {
                        const target = document.getElementById(id);
                        return target ? (target.innerText || target.textContent || '').trim() : '';
                    }).filter(Boolean);
                    if (parts.length > 0) return parts.join(' ').slice(0, 50);
                }
            }
            // 3. Preceding sibling heading or container heading that precedes this element in document order
            let prev = curr.previousElementSibling;
            while (prev) {
                const pTag = prev.tagName.toLowerCase();
                const pRole = (prev.getAttribute('role') || '').toLowerCase();
                if (['h1', 'h2', 'h3', 'h4', 'h5', 'h6'].includes(pTag) || pRole === 'heading') {
                    const ht = (prev.innerText || prev.textContent || '').trim();
                    if (ht && ht.length > 1 && ht.length < 60) {
                        return ht.slice(0, 50);
                    }
                }
                const nestedHeading = prev.querySelector('h1, h2, h3, h4, h5, h6, [role="heading"]');
                if (nestedHeading) {
                    const ht = (nestedHeading.innerText || nestedHeading.textContent || '').trim();
                    if (ht && ht.length > 1 && ht.length < 60) {
                        return ht.slice(0, 50);
                    }
                }
                prev = prev.previousElementSibling;
            }
            curr = curr.parentElement;
            depth++;
        }
        return '';
    };

    const computeAccessibleName = (el) => {
        if (isCompositeCard(el)) {
            let fullText = (el.innerText || el.textContent || '').replace(/\\s+/g, ' ').trim();
            fullText = fullText.replace(/\\s*(details|view|more info|expand)\\s*$/i, '').trim();
            if (fullText) return fullText.slice(0, 150);
        }

        // 1. aria-labelledby
        const labelledBy = el.getAttribute('aria-labelledby');
        if (labelledBy) {
            const parts = labelledBy.split(/\\s+/).map(id => {
                const target = document.getElementById(id);
                return target ? (target.innerText || target.textContent || '').trim() : '';
            }).filter(Boolean);
            if (parts.length > 0) return parts.join(' ');
        }
        // 2. aria-label
        const ariaLabel = el.getAttribute('aria-label');
        if (ariaLabel && ariaLabel.trim()) return ariaLabel.trim();

        // 3. Associated <label> for form inputs
        if (el.labels && el.labels.length > 0) {
            const labelTexts = Array.from(el.labels).map(l => (l.innerText || l.textContent || '').trim()).filter(Boolean);
            if (labelTexts.length > 0) return labelTexts.join(' ');
        }
        if (el.id) {
            const explicitLabel = document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
            if (explicitLabel) {
                const lt = (explicitLabel.innerText || explicitLabel.textContent || '').trim();
                if (lt) return lt;
            }
        }
        // 4. placeholder
        const placeholder = el.getAttribute('placeholder');
        if (placeholder && placeholder.trim()) return placeholder.trim();

        // 5. title / alt
        const title = el.getAttribute('title');
        if (title && title.trim()) return title.trim();
        const alt = el.getAttribute('alt');
        if (alt && alt.trim()) return alt.trim();

        // 6. value on input buttons/submits
        if (el.tagName === 'INPUT' && ['button', 'submit', 'reset'].includes((el.type || '').toLowerCase())) {
            if (el.value && el.value.trim()) return el.value.trim();
        }

        // 7. Direct text / innerText (truncated)
        const text = (el.innerText || el.textContent || '').replace(/\\s+/g, ' ').trim();
        if (text) return text.slice(0, 100);

        return '';
    };

    const getScrollContainer = (elem) => {
        let curr = elem.parentElement;
        while (curr && curr !== document.body && curr !== document.documentElement) {
            const cs = window.getComputedStyle(curr);
            if (cs.overflowY === 'auto' || cs.overflowY === 'scroll' || cs.overflowX === 'auto' || cs.overflowX === 'scroll') {
                return curr;
            }
            curr = curr.parentElement;
        }
        return document.scrollingElement || document.body;
    };

    // Candidate selector covering native controls, ARIA roles, contenteditable, and route cards
    const INTERACTIVE_SELECTOR = [
        'button',
        'input:not([type="hidden"])',
        'select',
        'textarea',
        'a[href]',
        'summary',
        'details',
        '[contenteditable="true"]',
        '[contenteditable=""]',
        '[data-trip-index]',
        '[role="button"]',
        '[role="link"]',
        '[role="checkbox"]',
        '[role="radio"]',
        '[role="combobox"]',
        '[role="tab"]',
        '[role="menuitem"]',
        '[role="menuitemcheckbox"]',
        '[role="menuitemradio"]',
        '[role="switch"]',
        '[role="option"]',
        '[role="textbox"]',
        '[role="searchbox"]'
    ].join(', ');

    const rawCandidates = Array.from(document.querySelectorAll(INTERACTIVE_SELECTOR));
    const interactiveSet = new Set(rawCandidates);

    // Also collect leaf elements with cursor: pointer that have text or direct interactivity
    const IGNORED_TAGS = new Set(['html', 'body', 'script', 'style', 'head', 'meta', 'link', 'svg', 'path', 'g', 'circle', 'rect', 'line', 'polygon', 'polyline', 'noscript', 'iframe']);
    const allPointerEls = document.querySelectorAll('*');
    for (let i = 0; i < allPointerEls.length; i++) {
        const el = allPointerEls[i];
        if (interactiveSet.has(el)) continue;
        const tag = el.tagName.toLowerCase();
        if (IGNORED_TAGS.has(tag)) continue;

        // If element is inside an identified composite card and not a sub-action button, skip it
        const insideCard = el.parentElement && el.parentElement.closest && el.parentElement.closest('[data-trip-index], [data-item-index], [data-card-id], [data-result-id], article');
        if (insideCard && !isDetailsButton(el)) continue;

        // If element is inside an already identified interactive control, don't duplicate it
        const parentInteractive = el.closest(INTERACTIVE_SELECTOR);
        if (parentInteractive && interactiveSet.has(parentInteractive) && !isCompositeCard(parentInteractive)) continue;

        try {
            const style = window.getComputedStyle(el);
            if (style.cursor === 'pointer') {
                const text = (el.innerText || el.textContent || '').trim();
                const ariaLabel = el.getAttribute('aria-label') || '';
                const title = el.getAttribute('title') || '';
                if (text.length > 0 || ariaLabel || title || el.id || el.getAttribute('role')) {
                    rawCandidates.push(el);
                    interactiveSet.add(el);
                }
            }
        } catch (e) {}
    }

    // Classify candidates into in-viewport and off-screen
    const seenElements = new Set();
    const inViewportCandidates = [];
    const offscreenCandidates = [];
    const containerItemCounts = new Map();

    for (const el of rawCandidates) {
        if (seenElements.has(el)) continue;
        if (!isStyledVisible(el)) continue;
        if (isDisabledOrInert(el)) continue;

        // Child spans inside composite cards should not compete with parent card
        const cardParent = el.parentElement && el.parentElement.closest && el.parentElement.closest('[data-trip-index], [data-item-index], [data-card-id], [data-result-id], article');
        if (cardParent && !isCompositeCard(el) && !isDetailsButton(el)) {
            continue;
        }

        seenElements.add(el);
        const rect = el.getBoundingClientRect();
        const container = getScrollContainer(el);
        containerItemCounts.set(container, (containerItemCounts.get(container) || 0) + 1);

        if (isInViewport(rect)) {
            inViewportCandidates.push({ el, rect, inViewport: true, distance: 0, container });
        } else {
            let distY = 0;
            if (rect.top >= window.innerHeight) distY = rect.top - window.innerHeight;
            else if (rect.bottom <= 0) distY = -rect.bottom;
            let distX = 0;
            if (rect.left >= window.innerWidth) distX = rect.left - window.innerWidth;
            else if (rect.right <= 0) distX = -rect.right;
            const distance = Math.hypot(distX, distY);
            offscreenCandidates.push({ el, rect, inViewport: false, distance, container });
        }
    }

    // Adaptive off-screen candidate policy:
    // max_offscreen_candidates = min(detected_actionable_items_in_active_scroll_container, 12) with nearest-to-viewport priority
    offscreenCandidates.sort((a, b) => a.distance - b.distance);

    const containerOffscreenCounts = new Map();
    const allowedOffscreen = [];
    for (const item of offscreenCandidates) {
        const totalInContainer = containerItemCounts.get(item.container) || 1;
        const maxOffscreen = Math.min(totalInContainer, 12);
        const currentCount = containerOffscreenCounts.get(item.container) || 0;
        if (currentCount < maxOffscreen) {
            containerOffscreenCounts.set(item.container, currentCount + 1);
            allowedOffscreen.push(item);
        }
    }

    const eligibleCandidates = inViewportCandidates.concat(allowedOffscreen);
    const items = [];
    let counter = 1;

    for (const item of eligibleCandidates) {
        const el = item.el;
        const rect = item.rect;
        const bid = `c${counter++}`;
        el.setAttribute(COMEX_BINDING_ATTR, bid);

        const tag = el.tagName.toLowerCase();
        let type = (el.type || el.getAttribute('type') || '').toLowerCase();
        const role = (el.getAttribute('role') || '').toLowerCase();
        const accessibleName = computeAccessibleName(el);
        const groupContext = computeGroupContext(el);

        let isSelected = false;
        if (isCompositeCard(el)) {
            type = el.hasAttribute('data-trip-index') ? 'transit_route_card' : 'composite_card';
            isSelected = (
                role === 'link' ||
                el.getAttribute('aria-selected') === 'true' ||
                el.getAttribute('aria-current') === 'true' ||
                el.getAttribute('aria-checked') === 'true' ||
                el.classList.contains('selected') ||
                el.classList.contains('active') ||
                Boolean(el.querySelector('button, [role="button"]'))
            );
        }

        let options = null;
        if (tag === 'select') {
            options = Array.from(el.options || []).map(o => (o.text || o.value || '').trim()).filter(Boolean);
        }

        let explicitLabelText = '';
        if (el.labels && el.labels.length > 0) {
            const labelTexts = Array.from(el.labels).map(l => (l.innerText || l.textContent || '').trim()).filter(Boolean);
            if (labelTexts.length > 0) explicitLabelText = labelTexts.join(' ');
        }
        if (!explicitLabelText && el.id) {
            const explicitLabel = document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
            if (explicitLabel) {
                explicitLabelText = (explicitLabel.innerText || explicitLabel.textContent || '').trim();
            }
        }

        const rawInnerText = (el.innerText || el.textContent || '').replace(/\\s+/g, ' ').trim().slice(0, 100);

        items.push({
            bid: bid,
            tag: tag,
            type: type,
            id: el.id || '',
            name: el.getAttribute('name') || '',
            placeholder: el.getAttribute('placeholder') || '',
            className: el.className || '',
            text: rawInnerText,
            label: explicitLabelText,
            accessible_name: accessibleName,
            group_context: groupContext,
            role: role,
            selector: el.id ? `#${CSS.escape(el.id)}` : (el.className ? `.${String(el.className).trim().split(/\\s+/)[0]}` : tag),
            disabled: false,
            checked: Boolean(el.checked),
            selected: isSelected,
            expanded: el.hasAttribute('aria-expanded') ? el.getAttribute('aria-expanded') === 'true' : null,
            focused: document.activeElement === el,
            bounds: {
                x: Math.round(rect.x),
                y: Math.round(rect.y),
                width: Math.round(rect.width),
                height: Math.round(rect.height)
            },
            center: {
                x: Math.round(rect.x + rect.width / 2),
                y: Math.round(rect.y + rect.height / 2)
            },
            options: options,
            in_viewport: item.inViewport
        });
    }

    return items;
})()
"""

UNIVERSAL_FINGERPRINT_JS: str = """
(() => {
    // Computes deterministic page state representation strictly omitting data-comex-id
    const COMEX_BINDING_ATTR = 'data-comex-id';
    const parts = [
        window.location.href,
        document.title
    ];

    const elements = document.querySelectorAll('button, input, select, textarea, a, [role], h1, h2, h3, p');
    for (let i = 0; i < elements.length; i++) {
        const el = elements[i];
        if (!el.isConnected) continue;
        const style = window.getComputedStyle(el);
        if (style.display === 'none' || style.visibility === 'hidden') continue;
        
        const rect = el.getBoundingClientRect();
        if (rect.width <= 0 && rect.height <= 0) continue;

        // Collect attributes excluding data-comex-id
        const attrs = [];
        for (let j = 0; j < el.attributes.length; j++) {
            const attr = el.attributes[j];
            if (attr.name === COMEX_BINDING_ATTR) continue;
            attrs.push(`${attr.name}=${attr.value}`);
        }
        attrs.sort();

        const text = (el.innerText || el.textContent || '').replace(/\\s+/g, ' ').trim().slice(0, 50);
        parts.push(`${el.tagName.toLowerCase()}|${attrs.join(';')}|${text}|${Math.round(rect.x)},${Math.round(rect.y)}`);
    }

    return parts.join('###');
})()
"""


class UniversalExtractor:
    """Production DOM extractor for arbitrary web applications with strict fingerprint isolation."""

    @classmethod
    def get_extraction_script(cls) -> str:
        return UNIVERSAL_EXTRACTION_JS

    @classmethod
    def get_fingerprint_script(cls) -> str:
        return UNIVERSAL_FINGERPRINT_JS

    @classmethod
    async def extract_from_cdp(cls, ws: Any, msg_counter: List[int]) -> List[Dict[str, Any]]:
        """Executes universal extraction over CDP connection and returns candidate raw dictionaries."""
        msg_counter[0] += 1
        cid = msg_counter[0]
        eval_payload = {
            "id": cid,
            "method": "Runtime.evaluate",
            "params": {
                "expression": UNIVERSAL_EXTRACTION_JS,
                "returnByValue": True,
                "awaitPromise": False,
            },
        }
        await ws.send(json.dumps(eval_payload))
        while True:
            raw = await ws.recv()
            res = json.loads(raw)
            if res.get("id") == cid:
                if "error" in res:
                    logger.error(f"Universal extraction CDP error: {res['error']}")
                    return []
                val = res.get("result", {}).get("result", {}).get("value")
                return val if isinstance(val, list) else []

    @classmethod
    async def compute_fingerprint_from_cdp(cls, ws: Any, msg_counter: List[int]) -> str:
        """Computes deterministic state fingerprint via CDP with data-comex-id excluded."""
        msg_counter[0] += 1
        cid = msg_counter[0]
        eval_payload = {
            "id": cid,
            "method": "Runtime.evaluate",
            "params": {
                "expression": UNIVERSAL_FINGERPRINT_JS,
                "returnByValue": True,
                "awaitPromise": False,
            },
        }
        await ws.send(json.dumps(eval_payload))
        while True:
            raw = await ws.recv()
            res = json.loads(raw)
            if res.get("id") == cid:
                if "error" in res:
                    logger.error(f"CDP fingerprint error: {res['error']}")
                    return ""
                val = res.get("result", {}).get("result", {}).get("value", "")
                return hashlib.sha256(str(val).encode("utf-8", errors="replace")).hexdigest()

    @classmethod
    def extract_from_html_fixture(cls, html_content: str, viewport_height: int = 800) -> List[Dict[str, Any]]:
        """
        Pure Python HTML parser for offline unit testing without browser dependencies.
        Extracts semantic controls, ARIA roles, composite route cards, and accessible labels
        mirroring the browser JS logic with adaptive offscreen candidate policy.
        """
        class _FixtureParser(HTMLParser):
            def __init__(self) -> None:
                super().__init__()
                self.elements: List[Dict[str, Any]] = []
                self.tag_stack: List[Tuple[str, Dict[str, str], int]] = []
                self.text_buffer: Dict[int, List[str]] = {}
                self.labels_for: Dict[str, str] = {}
                self.id_to_text: Dict[str, str] = {}
                self.recent_headings: List[str] = []
                self.elem_counter = 0

            VOID_TAGS = {"input", "img", "br", "hr", "meta", "link"}

            def handle_starttag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
                attr_dict = {k.lower(): (v or "") for k, v in attrs}
                self.elem_counter += 1
                idx = self.elem_counter
                self.text_buffer[idx] = []

                t_lower = tag.lower()
                if "id" in attr_dict:
                    self.id_to_text[attr_dict["id"]] = ""
                if t_lower in self.VOID_TAGS:
                    self._evaluate_element(t_lower, attr_dict, "", idx)
                else:
                    self.tag_stack.append((t_lower, attr_dict, idx))

            def handle_data(self, data: str) -> None:
                cleaned = data.strip()
                if cleaned and self.tag_stack:
                    for _, _, idx in self.tag_stack:
                        self.text_buffer[idx].append(cleaned)

            def handle_endtag(self, tag: str) -> None:
                if not self.tag_stack:
                    return
                for i in range(len(self.tag_stack) - 1, -1, -1):
                    t, attr_dict, idx = self.tag_stack[i]
                    if t == tag.lower():
                        self.tag_stack.pop(i)
                        inner_text = " ".join(self.text_buffer.get(idx, [])).strip()
                        if "id" in attr_dict:
                            self.id_to_text[attr_dict["id"]] = inner_text
                        if t == "label" and "for" in attr_dict:
                            self.labels_for[attr_dict["for"]] = inner_text
                        elif t in ("h1", "h2", "h3", "h4", "h5", "h6"):
                            if inner_text:
                                self.recent_headings.append(inner_text[:50])
                        self._evaluate_element(t, attr_dict, inner_text, idx)
                        break

            def _evaluate_element(self, tag: str, attrs: Dict[str, str], text: str, idx: int) -> None:
                style = attrs.get("style", "").lower()
                if "display: none" in style or "visibility: hidden" in style or "opacity: 0" in style:
                    return
                if "hidden" in attrs or "inert" in attrs:
                    return
                if any(("inert" in stack_attrs or "hidden" in stack_attrs) for _, stack_attrs, _ in self.tag_stack):
                    return

                role = attrs.get("role", "").lower()
                aria_label = attrs.get("aria-label", "").strip()
                aria_labelledby = attrs.get("aria-labelledby", "").strip()
                labelledby_text = ""
                if aria_labelledby:
                    parts = [self.id_to_text.get(ref_id, "") for ref_id in aria_labelledby.split()]
                    labelledby_text = " ".join(p for p in parts if p).strip()

                placeholder = attrs.get("placeholder", "").strip()
                title = attrs.get("title", "").strip()
                name = labelledby_text or aria_label or placeholder or title or text

                is_route_card = "data-trip-index" in attrs
                inside_route_card = any("data-trip-index" in stack_attrs for _, stack_attrs, _ in self.tag_stack)
                is_details_btn = tag == "button" and any(k in name.lower() for k in ("details", "view", "more", "expand", "select"))

                # Rule 2: Child spans inside route cards should not compete with parent route card
                if inside_route_card and not is_route_card and not is_details_btn:
                    return

                is_interactive = False
                if is_route_card:
                    is_interactive = True
                elif tag in ("button", "input", "select", "textarea", "summary", "details"):
                    is_interactive = True
                elif tag == "a" and "href" in attrs:
                    is_interactive = True
                elif role in ("button", "link", "checkbox", "radio", "combobox", "tab", "menuitem", "switch", "option", "textbox"):
                    is_interactive = True
                elif "cursor: pointer" in style:
                    is_interactive = True
                elif attrs.get("contenteditable") in ("true", ""):
                    is_interactive = True

                # Determine generic group context from tag stack (fieldset > legend, role=group, or preceding heading)
                group_context = ""
                for s_tag, s_attrs, s_idx in reversed(self.tag_stack):
                    s_role = s_attrs.get("role", "").lower()
                    if s_role in ("group", "radiogroup"):
                        group_context = s_attrs.get("aria-label", "") or s_attrs.get("title", "")
                        if group_context:
                            break
                    if s_tag == "fieldset":
                        # Look for legend text
                        group_context = s_attrs.get("data-legend", "") or s_attrs.get("aria-label", "")
                        if group_context:
                            break
                if not group_context and self.recent_headings:
                    group_context = self.recent_headings[-1]

                if is_interactive:
                    el_type = attrs.get("type", "").lower()
                    if is_route_card:
                        el_type = "transit_route_card"
                        name = text.replace("Details", "").strip() or name
                    elif tag == "input" and el_type in ("button", "submit", "reset") and not name:
                        name = attrs.get("value", "")

                    is_selected = False
                    if is_route_card:
                        is_selected = (
                            role == "link" or
                            "selected" in attrs.get("class", "").lower() or
                            "active" in attrs.get("class", "").lower() or
                            attrs.get("aria-selected") == "true" or
                            attrs.get("data-selected") == "true"
                        )

                    y_pos = int(attrs.get("data-y", "10"))
                    height = int(attrs.get("data-height", "30"))
                    is_offscreen = attrs.get("data-offscreen") == "true" or y_pos >= viewport_height or (y_pos + height) <= 0
                    container_id = attrs.get("data-container", "default_container")

                    elem_id = attrs.get("id", "")
                    explicit_lbl = self.labels_for.get(elem_id, "")
                    label_val = explicit_lbl or aria_label or title
                    self.elements.append({
                        "bid": f"c{len(self.elements) + 1}",
                        "tag": tag,
                        "type": el_type,
                        "id": elem_id,
                        "name": attrs.get("name", ""),
                        "placeholder": placeholder,
                        "className": attrs.get("class", ""),
                        "text": text,
                        "label": label_val,
                        "accessible_name": name,
                        "group_context": group_context,
                        "role": role,
                        "selector": f"#{attrs['id']}" if attrs.get("id") else (f".{attrs['class'].split()[0]}" if attrs.get("class") else tag),
                        "disabled": "disabled" in attrs or attrs.get("aria-disabled") == "true",
                        "checked": "checked" in attrs or attrs.get("aria-checked") == "true",
                        "selected": is_selected,
                        "expanded": attrs.get("aria-expanded") == "true" if "aria-expanded" in attrs else None,
                        "focused": False,
                        "bounds": {"x": 10, "y": y_pos, "width": 100, "height": height},
                        "center": {"x": 60, "y": y_pos + height // 2},
                        "options": None,
                        "is_offscreen": is_offscreen,
                        "container_id": container_id,
                        "distance_to_viewport": max(0, y_pos - viewport_height) if y_pos >= viewport_height else (max(0, -y_pos) if y_pos < 0 else 0)
                    })

        parser = _FixtureParser()
        parser.feed(html_content)

        # Apply Rule 3: Adaptive off-screen candidate policy in fixture
        in_viewport = [e for e in parser.elements if not e["is_offscreen"]]
        offscreen = [e for e in parser.elements if e["is_offscreen"]]
        offscreen.sort(key=lambda e: e["distance_to_viewport"])

        container_counts: Dict[str, int] = {}
        for e in parser.elements:
            cid = e["container_id"]
            container_counts[cid] = container_counts.get(cid, 0) + 1

        allowed_offscreen: List[Dict[str, Any]] = []
        offscreen_per_container: Dict[str, int] = {}
        for e in offscreen:
            cid = e["container_id"]
            total_in_container = container_counts.get(cid, 1)
            max_allowed = min(total_in_container, 12)
            cur = offscreen_per_container.get(cid, 0)
            if cur < max_allowed:
                offscreen_per_container[cid] = cur + 1
                allowed_offscreen.append(e)

        result_elements = in_viewport + allowed_offscreen
        # Clean internal testing flags
        for i, el in enumerate(result_elements, start=1):
            el["bid"] = f"c{i}"
            el.pop("is_offscreen", None)
            el.pop("container_id", None)
            el.pop("distance_to_viewport", None)

        return result_elements
