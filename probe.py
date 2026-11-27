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
        self.embedded = 0
        self.embed_urls = []
    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag in ("button", "input"):
            self.controls += 1
            self.control_attrs.append(a)
        if tag == "form":
            self.forms.append(a)
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
        if self.anchor is not None:
            self.anchor[1] += " " + data
    def handle_endtag(self, tag):
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
