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
            seats = await frame.evaluate("""() => {
                const nodes = Array.from(document.querySelectorAll('[id][title][class]')).filter(e =>
                    !e.children.length && /free|available|voln/i.test(e.getAttribute('class')) &&
                    !/occupied|reserved|sold|obsazen|prodan|unavailable/i.test(e.getAttribute('class')));
                return nodes.map(e => ({
                    id:e.id, title:e.title,
                    attrs:Array.from(e.attributes).filter(a=>a.name.startsWith('data-')).map(a=>({name:a.name,value:a.value})),
                    parents:[e.parentElement,e.parentElement?.parentElement].filter(Boolean).map(p=>({id:p.id, title:p.title, numeric:/^\\s*\\d+\\s*$/.test(p.textContent)}))
                }));
            }""")
            shapes = Counter()
            fields = {}
            for seat in seats:
                title = seat["title"]
                shape = re.sub(r"\d+", "0", title)
                shape = re.sub(r"[^\W\d_]+", "W", shape)
                shape = re.sub(r"[^0W\s:;,./()|+-]", "?", shape)
                shapes[shape[:120]] += 1
                for a in seat["attrs"]:
                    fields.setdefault(a["name"], []).append(a["value"])
            for i, (shape, count) in enumerate(shapes.most_common(8), 1):
                print(f"title_shape={i} count={count} pattern={json.dumps(shape)}")
            for i, values in enumerate(fields.values(), 1):
                print(f"data_field={i} present={len(values)} distinct={len(set(values))} numeric={sum(v.isdecimal() for v in values)} json_like={sum(v.startswith(('{','[')) for v in values)}")
            print(f"parent_ids={sum(any(p['id'] for p in s['parents']) for s in seats)} parent_titles={sum(any(p['title'] for p in s['parents']) for s in seats)}")
            from html import unescape
            from html.parser import HTMLParser
            class LabelText(HTMLParser):
                def __init__(self):
                    super().__init__()
                    self.parts = []
                def handle_data(self, data):
                    self.parts.append(data)
            for i, values in enumerate(fields.values(), 1):
                if len(set(values)) < 2:
                    continue
                shapes = Counter()
                row_hits = seat_hits = 0
                for value in values:
                    value = unescape(value)
                    parser = LabelText()
                    parser.feed(value)
                    plain = " ".join(parser.parts)
                    row_hits += int(bool(re.search(r"řad|row", plain, re.I)))
                    seat_hits += int(bool(re.search(r"sedadl|místo|seat", plain, re.I)))
                    shape = re.sub(r"\d+", "0", plain)
                    shape = re.sub(r"[^\W\d_]+", "W", shape)
                    shape = re.sub(r"[^0W\s:;,./()|+-]", "?", shape)
                    shapes[shape[:120]] += 1
                print(f"field_detail={i} row_hints={row_hits} seat_hints={seat_hits}")
                for shape, count in shapes.most_common(3):
                    print(f"field_shape={i} count={count} pattern={json.dumps(shape)}")
            globals_stats = await frame.evaluate("""() => {
                const hints = /seat|sedadl|hledi|availability|occupied|free|reserved/i;
                return Object.keys(window).filter(k=>hints.test(k)).slice(0,30).map(k=>{
                    const d = Object.getOwnPropertyDescriptor(window,k);
                    const v = d && 'value' in d ? d.value : undefined;
                    const sample = Array.isArray(v) ? v.slice(0,20) : [v];
                    const keys = new Set();
                    for (const x of sample) if(x && typeof x==='object') for(const a of Object.keys(x).slice(0,100)) keys.add(a);
                    return {kind:Array.isArray(v)?'array':typeof v, size:Array.isArray(v)?v.length:keys.size,
                        row_keys:[...keys].filter(k=>/row|rada|řad/i.test(k)).length,
                        seat_keys:[...keys].filter(k=>/seat|sedadl|place/i.test(k)).length};
                });
            }""")
            for i, item in enumerate(globals_stats, 1):
                print(f"global_group={i} " + json.dumps(item, sort_keys=True))
            rows = Counter()
            pairs = set()
            sectors = Counter()
            ground_rows = Counter()
            ground_pairs = set()
            parsed = 0
            for seat in seats:
                labels = []
                for a in seat["attrs"]:
                    parser = LabelText()
                    parser.feed(unescape(a["value"]))
                    labels.append(" ".join(parser.parts))
                title = " ".join(labels)
                row = re.search(r"(?:řad[ayěu]?|row)\s*[:.\-]?\s*(\d+)", title, re.I)
                number = re.search(r"(?:sedadlo|sedadla|místo|seat)\s*[:.\-]?\s*(\d+)", title, re.I)
                sector = "unknown"
                if re.search(r"balk[oó]n|balcony|l[oó]ž|box|přístav", title, re.I):
                    sector = "excluded"
                elif re.search(r"přízemí|prizemi|stalls|ground floor", title, re.I):
                    sector = "ground"
                sectors[sector] += 1
                if row:
                    n = int(row.group(1))
                    rows[n] += 1
                    if number:
                        parsed += 1
                        pairs.add((n, int(number.group(1))))
                        if sector == "ground":
                            ground_rows[n] += 1
                            ground_pairs.add((n, int(number.group(1))))
            limit = int(config.get("seats", {}).get("max_row", 5))
            print(f"free_candidates={len(seats)} unique_ids={len({s['id'] for s in seats})} parsed_rows={sum(rows.values())} parsed_seat_pairs={parsed}")
            print(f"front_row_candidates={sum(v for k,v in rows.items() if 1 <= k <= limit)} adjacent_number_pairs={sum(1 for r,n in pairs if 1 <= r <= limit and (r,n+1) in pairs)}")
            print("sector_counts=" + json.dumps(sectors, sort_keys=True))
            print("ground_front_row_counts=" + json.dumps({r:ground_rows[r] for r in range(1,limit+1)}))
            print(f"ground_front_candidates={sum(v for k,v in ground_rows.items() if 1 <= k <= limit)}")
            print("front_row_counts=" + json.dumps({r:rows[r] for r in range(1,limit+1)}))
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
