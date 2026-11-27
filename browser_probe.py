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
        program_targets = {x["url"] for x in matching if x["url"]}
        detail_targets = set()
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
                            kind: e.tagName,
                            url: e.tagName === 'A' ? e.href : ''
                        }))""")
                        matched = [x for x in items if any(w.casefold() in x["label"].casefold() for w in labels)]
                        detail_targets.update(x["url"] for x in matched if x["url"])
                        print(f"frame_controls={len(items)} purchase_matches={len(matched)}")
                except Exception:
                    print(f"detail_sample={i} failed")
        print(f"unique_purchase_targets={len(program_targets | detail_targets)} shared_targets={len(program_targets & detail_targets)}")
        targets = sorted(u for u in program_targets | detail_targets if urlsplit(u).scheme == "https")
        if config.get("sample_url"):
            if urlsplit(config["sample_url"]).scheme != "https":
                raise ValueError()
            targets = [config["sample_url"]]
            print("configured_sample=1")
        for i, target in enumerate(targets[:2], 1):
            try:
                await page.goto(target, wait_until="domcontentloaded", timeout=20000)
                await page.wait_for_timeout(3000)
                print(f"purchase_sample={i} frames={len(page.frames)} external_origin={int(urlsplit(page.url).netloc != origin)}")
                for frame in page.frames:
                    print(f"purchase_frame_controls={await frame.locator('a,button,input').count()} seat_attribute_hints={await frame.locator('[data-seat],[data-row],[data-seat-id]').count()}")
            except Exception:
                print(f"purchase_sample={i} failed")
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
