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
            await response.finished()
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
        page.on("response", lambda r: pending.append(asyncio.create_task(inspect(r))))
        await page.goto(config["target_url"], wait_until="domcontentloaded", timeout=25000)
        await page.wait_for_timeout(3000)
        labels = config.get("link_labels", ["koupit", "vstupenky", "buy", "tickets"])
        candidates = await page.locator("a[href], button, input[type=button]").evaluate_all("""els => els.map(e => ({
            label: [e.innerText,e.title,e.getAttribute('aria-label'),e.value].filter(Boolean).join(' '),
            url: e.tagName === 'A' ? e.href : '',
            kind: e.tagName
        }))""")
        matching = [x for x in candidates if any(w.casefold() in x["label"].casefold() for w in labels)]
        print(f"rendered_purchase_links={sum(bool(x['url']) for x in matching)} rendered_purchase_controls={sum(not x['url'] for x in matching)}")
        print(f"program_frames={len(page.frames)}")
        links = await page.locator("a[href]").evaluate_all("(els) => els.map(e => ({url:e.href,label:e.innerText}))")
        from urllib.parse import urlsplit
        origin = urlsplit(page.url).netloc
        groups = {}
        for link in links:
            u = urlsplit(link["url"])
            parts = u.path.strip("/").split("/")
            if u.scheme == "https" and u.netloc == origin and not u.query and not u.fragment and len(parts) >= 2 and link["label"].strip():
                groups.setdefault(tuple(parts[:-1]), set()).add(link["url"])
        if groups:
            for i, url in enumerate(sorted(max(groups.values(), key=len))[:2], 1):
                try:
                    await page.goto(url, wait_until="domcontentloaded", timeout=20000)
                    await page.wait_for_timeout(3000)
                    print(f"detail_sample={i} frames={len(page.frames)}")
                    for frame in page.frames:
                        items = await frame.locator("a[href], button, input[type=button]").evaluate_all("""els => els.map(e => ({
                            label: [e.innerText,e.title,e.getAttribute('aria-label'),e.value].filter(Boolean).join(' '),
                            kind: e.tagName
                        }))""")
                        matched = [x for x in items if any(w.casefold() in x["label"].casefold() for w in labels)]
                        print(f"frame_controls={len(items)} purchase_matches={len(matched)}")
                except Exception:
                    print(f"detail_sample={i} failed")
        if pending:
            await asyncio.wait(pending, timeout=5)
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
