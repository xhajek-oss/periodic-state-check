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
    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
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
