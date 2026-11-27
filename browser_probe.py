import asyncio
import json
import os
import time
from collections import Counter
from playwright.async_api import async_playwright

async def main():
    start = time.monotonic()
    config = json.loads(os.environ["MONITOR_CONFIG"])
    counts = Counter()
    pending = []
    async def inspect(response):
        try:
            counts["responses"] += 1
            if "json" not in response.headers.get("content-type", "").lower():
                return
            counts["json_responses"] += 1
            if int(response.headers.get("content-length", "0")) > 2000000:
                return
            data = await response.json()
            stack = [data]
            keys = set()
            while stack and len(keys) < 1000:
                node = stack.pop()
                if isinstance(node, dict):
                    keys.update(str(k).lower() for k in node)
                    stack.extend(node.values())
                elif isinstance(node, list):
                    stack.extend(node[:1000])
            if any("seat" in k for k in keys):
                counts["seat_key_responses"] += 1
            if any("row" in k for k in keys):
                counts["row_key_responses"] += 1
            if any("availab" in k or "occup" in k for k in keys):
                counts["availability_key_responses"] += 1
        except Exception:
            counts["inspection_errors"] += 1
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page()
        def on_response(response):
            pending.append(asyncio.create_task(inspect(response)))
        page.on("response", on_response)
        from urllib.parse import urlsplit
        from probe import fetch
        target = config["sample_url"]
        if urlsplit(target).scheme != "https":
            raise ValueError()
        import re
        try:
            raw, _, status, seconds = await asyncio.to_thread(fetch, target)
            print(f"http_status={status} http_seconds={seconds:.2f}")
            print(f"http_scripts={raw.scripts} http_controls={raw.controls}")
            print(f"http_class_attributes={sum(a == 'class' for a in raw.attrs)}")
            print(f"http_seat_hints={sum(bool(re.search(r'seat|sedadl|hledi|availability', x, re.I)) for x in raw.parts)}")
        except Exception:
            print("http_sample_failed")
        response = await page.goto(target, wait_until="domcontentloaded", timeout=25000)
        await page.wait_for_timeout(5000)
        print(f"browser_status={response.status if response else 0} frames={len(page.frames)}")
        for frame in page.frames:
            stats = await frame.evaluate("""() => {
                const all = Array.from(document.querySelectorAll('*'));
                const text = document.body?.innerText || '';
                const hints = /seat|sedadl|hledi|availability|occupied|free|reserved/i;
                const globals = Object.keys(window).filter(k => hints.test(k));
                return {
                    elements: all.length,
                    svg: document.querySelectorAll('svg').length,
                    svg_shapes: document.querySelectorAll('svg circle,svg rect,svg path,svg use').length,
                    canvas: document.querySelectorAll('canvas').length,
                    images: document.images.length,
                    class_hints: all.filter(e => hints.test(e.getAttribute('class') || '')).length,
                    attribute_hints: all.filter(e => Array.from(e.attributes).some(a => hints.test(a.name))).length,
                    global_hints: globals.length,
                    row_text: Number(/\\b(row|řada|řady)\\b/i.test(text)),
                    seat_text: Number(/seat|sedadl|hledi/i.test(text)),
                    error_text: Number(/expired|invalid|not found|chyba|neplatn|vypršel/i.test(text)),
                    login_text: Number(/sign in|log in|přihlásit/i.test(text))
                };
            }""")
            print("rendered_stats=" + json.dumps(stats, sort_keys=True))
            structure = await frame.evaluate("""() => {
                const hints = /seat|sedadl|hledi|availability|occupied|free|reserved/i;
                const nodes = Array.from(document.querySelectorAll('[class]')).filter(e => hints.test(e.className?.baseVal || e.getAttribute('class') || ''));
                const groups = new Map();
                for (const e of nodes) {
                    const key = e.tagName + '|' + e.getAttribute('class');
                    if (!groups.has(key)) groups.set(key, []);
                    groups.get(key).push(e);
                }
                return Array.from(groups.values()).sort((a,b)=>b.length-a.length).slice(0,15).map(g => ({
                    count:g.length,
                    leaf:g.filter(e=>!e.children.length).length,
                    titled:g.filter(e=>e.hasAttribute('title')).length,
                    identified:g.filter(e=>e.id).length,
                    numeric_text:g.filter(e=>/^\\s*\\d+\\s*$/.test(e.textContent)).length,
                    data_attributes:g.filter(e=>Array.from(e.attributes).some(a=>a.name.startsWith('data-'))).length,
                    free_hint:Number(/free|available|voln/i.test(g[0].getAttribute('class'))),
                    occupied_hint:Number(/occupied|reserved|sold|obsazen|prodan/i.test(g[0].getAttribute('class'))),
                    row_hint:Number(/row|rada/i.test(g[0].getAttribute('class')))
                }));
            }""")
            for i, group in enumerate(structure, 1):
                print(f"element_group={i} " + json.dumps(group, sort_keys=True))
        page.remove_listener("response", on_response)
        if pending:
            _, unfinished = await asyncio.wait(pending, timeout=5)
            for task in unfinished:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
        await browser.close()
    for key in sorted(counts):
        print(f"{key}={counts[key]}")
    print(f"total_seconds={time.monotonic()-start:.2f}")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception:
        print("browser_probe_failed")
        raise SystemExit(1) from None
