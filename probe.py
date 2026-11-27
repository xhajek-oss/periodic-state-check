import json
import os
import re
import time
import urllib.request
from urllib.parse import urljoin, urlsplit
from html.parser import HTMLParser

class Probe(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []
        self.scripts = 0
        self.parts = []
        self.anchor = None
        self.attrs = []
        self.controls = 0
        self.control_attrs = []
        self.forms = []
        self.form_records = []
        self.active_form = None
        self.embedded = 0
        self.embed_urls = []
    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag in ("button", "input"):
            self.controls += 1
            self.control_attrs.append(a)
            if self.active_form is not None:
                self.active_form['controls'].append(a)
        if tag == "form":
            self.forms.append(a)
            self.active_form = {'attrs': a, 'controls': [], 'text': []}
            self.form_records.append(self.active_form)
        if tag in ("iframe", "object", "embed"):
            self.embedded += 1
            if tag == 'iframe' and a.get('src'):
                self.embed_urls.append(a['src'])
        self.attrs.extend(k for k, v in attrs)
        if tag == "a":
            self.anchor = [a.get("href") or "", a.get("title") or ""]
        elif tag == "script":
            self.scripts += 1
    def handle_data(self, data):
        self.parts.append(data)
        if self.active_form is not None:
            self.active_form['text'].append(data)
        if self.anchor is not None:
            self.anchor[1] += " " + data
    def handle_endtag(self, tag):
        if tag == "form":
            self.active_form = None
        if tag == "a" and self.anchor is not None:
            self.links.append(self.anchor)
            self.anchor = None

def fetch(url):
    if urlsplit(url).scheme != "https":
        raise ValueError()
    req = urllib.request.Request(url, headers={"User-Agent": "ResourceMonitor/0.2"})
    start = time.monotonic()
    with urllib.request.urlopen(req, timeout=12) as response:
        body = response.read(4000001)
        if len(body) > 4000000:
            raise ValueError()
        status = response.status
        final = response.geturl()
    parser = Probe()
    parser.feed(body.decode("utf-8", errors="replace"))
    return parser, final, status, time.monotonic()-start

def main():
    start = time.monotonic()
    try:
        config = json.loads(os.environ["MONITOR_CONFIG"])
        parser, base, status, elapsed = fetch(config["target_url"])
        print(f"status={status} fetch_seconds={elapsed:.2f}")
        words = config.get("keywords", [])
        matches = sum(len(re.findall(r"\b"+re.escape(w)+r"\b", " ".join(parser.parts), re.I)) for w in words)
        print(f"keyword_matches={matches} links={len(parser.links)}")
        # Only follow explicitly labelled public purchase landing links.
        labels = config.get("link_labels", ["koupit", "buy", "tickets"])
        candidates = []
        for href, label in parser.links:
            if not any(w.casefold() in label.casefold() for w in labels):
                continue
            url = urljoin(base, href)
            if urlsplit(url).scheme == "https" and url not in candidates:
                candidates.append(url)
        print(f"candidate_links={len(candidates)}")
        print(f"controls={parser.controls} embedded={parser.embedded}")
        from collections import Counter
        types = Counter()
        for a in parser.control_attrs:
            t = (a.get("type") or "").lower()
            types[t if t in ("button", "submit", "hidden", "text", "checkbox", "radio") else "other"] += 1
        for t, n in sorted(types.items()):
            print(f"control_type={t} count={n}")
        for label, pattern in (
            ("data", r"^data-"),
            ("handler", r"^on"),
            ("identifier", r"id|event|performance|show"),
            ("destination", r"url|href|action|target"),
            ("purchase", r"ticket|buy|booking|sale|reserve"),
        ):
            hits = sum(any(re.search(pattern, k, re.I) for k in a) for a in parser.control_attrs)
            print(f"control_attribute_category={label} count={hits}")
        print(f"forms={len(parser.forms)} forms_with_action={sum(bool(a.get('action')) for a in parser.forms)}")
        print(f"controls_with_https_value={sum(any(isinstance(v,str) and 'https://' in v for v in a.values()) for a in parser.control_attrs)}")
        print(f"controls_with_purchase_value={sum(any(isinstance(v,str) and any(w.casefold() in v.casefold() for w in labels) for v in a.values()) for a in parser.control_attrs)}")
        # Compare form structure and values locally; emit aggregate counts only.
        actions = set()
        destinations = set()
        external_forms = 0
        keyword_forms = 0
        purchase_forms = 0
        field_values = {}
        for record in parser.form_records:
            a = record["attrs"]
            action = urlsplit(urljoin(base, a.get("action") or ""))
            actions.add((action.netloc, action.path))
            external_forms += int(action.netloc != urlsplit(base).netloc)
            values = []
            for field in record["controls"]:
                value = field.get("value") or ""
                name = field.get("name") or ""
                if name and value:
                    field_values.setdefault(name, set()).add(value)
                values.append(value)
                if value.startswith("https://"):
                    destinations.add(value)
            combined = " ".join(record["text"] + values)
            keyword_forms += int(any(re.search(r"\b"+re.escape(w)+r"\b", combined, re.I) for w in words))
            purchase_forms += int(any(w.casefold() in combined.casefold() for w in labels))
        print(f"form_action_paths={len(actions)} form_action_domains={len({x[0] for x in actions})} external_forms={external_forms}")
        print(f"form_methods_get={sum((a.get('method') or 'get').lower() == 'get' for a in parser.forms)} form_methods_post={sum((a.get('method') or '').lower() == 'post' for a in parser.forms)}")
        print(f"form_keyword_matches={keyword_forms} form_purchase_matches={purchase_forms}")
        print(f"unique_field_urls={len(destinations)} field_url_domains={len({urlsplit(u).netloc for u in destinations})} external_field_urls={sum(urlsplit(u).netloc != urlsplit(base).netloc for u in destinations)}")
        print(f"distinct_field_names={len(field_values)} varying_fields={sum(len(v)>1 for v in field_values.values())} varying_numeric_fields={sum(len(v)>1 and all(x.isdecimal() for x in v) for v in field_values.values())}")
        # Sample same-origin detail-like paths without disclosing their values.
        from collections import Counter
        origin = urlsplit(base).netloc
        groups = {}
        for href, label in parser.links:
            u = urljoin(base, href)
            p = urlsplit(u)
            if p.scheme != "https" or p.netloc != origin or p.query or p.fragment:
                continue
            segments = p.path.strip("/").split("/")
            if len(segments) < 2 or not label.strip():
                continue
            groups.setdefault(tuple(segments[:-1]), set()).add(u)
        ranked = sorted(groups.values(), key=len, reverse=True)
        print(f"path_groups={len(ranked)} group_sizes={','.join(str(len(g)) for g in ranked[:8])}")
        sample = urlsplit(config.get("sample_url", ""))
        booking_origin = sample.netloc
        matched = 0
        checked = 0
        failed = 0
        booking_targets = set()
        if ranked:
            for url in sorted(ranked[0])[:40]:
                try:
                    detail, detail_base, code, seconds = fetch(url)
                    checked += 1
                    if not any(re.search(r"\b"+re.escape(w)+r"\b", " ".join(detail.parts), re.I) for w in words):
                        continue
                    matched += 1
                    targets = set()
                    values = [h for h, _ in detail.links] + detail.embed_urls
                    values += [v for a in detail.control_attrs + detail.forms for v in a.values() if isinstance(v,str)]
                    for value in values:
                        u = urlsplit(urljoin(detail_base, value))
                        if booking_origin and u.scheme == "https" and u.netloc == booking_origin:
                            targets.add(u.geturl())
                    booking_targets.update(targets)
                    print(f"matched_detail={matched} booking_origin_links={len(targets)}")
                except Exception:
                    failed += 1
        print(f"discovery_checked={checked} discovery_failed={failed} discovery_keyword_matches={matched} discovery_unique_booking_targets={len(booking_targets)}")
        sample_parts = sample.path.strip("/").split("/")
        for i, target in enumerate(sorted(booking_targets), 1):
            parts = urlsplit(target).path.strip("/").split("/")
            prefix = 0
            for a, b in zip(parts, sample_parts):
                if a != b:
                    break
                prefix += 1
            print(f"booking_target={i} same_sample_path={int(parts == sample_parts)} shared_path_segments={prefix} path_depth={len(parts)} numeric_segments={sum(p.isdecimal() for p in parts)}")
            try:
                landing, final, code, seconds = fetch(target)
                final_parts = urlsplit(final).path.strip("/").split("/")
                print(f"booking_landing={i} status={code} same_origin={int(urlsplit(final).netloc == booking_origin)} same_sample_path={int(final_parts == sample_parts)} scripts={landing.scripts} controls={landing.controls}")
            except Exception:
                print(f"booking_landing={i} fetch_failed")
        month_links = set()
        for href, label in parser.links:
            if re.search(r"leden|únor|březen|duben|květen|červen|červenec|srpen|září|říjen|listopad|prosinec|january|february|march|april|may|june|july|august|september|october|november|december", label, re.I):
                u = urlsplit(urljoin(base, href))
                if u.scheme == "https" and u.netloc == origin:
                    month_links.add(u.geturl())
        print(f"month_label_links={len(month_links)}")
        if not candidates and ranked:
            for i, url in enumerate(sorted(ranked[0])[:2], 1):
                try:
                    detail, detail_base, code, seconds = fetch(url)
                    found = sum(any(w.casefold() in label.casefold() for w in labels) for _, label in detail.links)
                    print(f"detail_sample={i} status={code} seconds={seconds:.2f} links={len(detail.links)} purchase_labels={found} controls={detail.controls} embedded={detail.embedded}")
                    for src in detail.embed_urls[:1]:
                        try:
                            target = urljoin(detail_base, src)
                            frame, _, frame_code, frame_seconds = fetch(target)
                            hints = sum(bool(re.search(r"seat|row|place|available", a, re.I)) for a in frame.attrs)
                            print(f"frame_sample={i} status={frame_code} seconds={frame_seconds:.2f} scripts={frame.scripts} links={len(frame.links)} attribute_hints={hints}")
                        except Exception:
                            print(f"frame_sample={i} fetch_failed")
                except Exception:
                    print(f"detail_sample={i} fetch_failed")
        for i, url in enumerate(candidates[:2], 1):
            try:
                page, _, code, seconds = fetch(url)
                attrs = sum(bool(re.search(r"seat|row|place|available", a, re.I)) for a in page.attrs)
                hints = sum(len(re.findall(r"\b"+w+r"\b", " ".join(page.parts), re.I)) for w in ("seat", "row", "available", "sedadlo", "řada"))
                print(f"sample={i} status={code} seconds={seconds:.2f} scripts={page.scripts} attribute_hints={attrs} text_hints={hints}")
            except Exception:
                print(f"sample={i} fetch_failed")
        print(f"total_seconds={time.monotonic()-start:.2f}")
        return 0
    except Exception:
        print(f"probe_failed elapsed_seconds={time.monotonic()-start:.2f}")
        return 1

if __name__ == "__main__":
    raise SystemExit(main())
