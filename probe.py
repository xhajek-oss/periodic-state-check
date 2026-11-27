import json
import os
import time
import urllib.request
from html.parser import HTMLParser

class Probe(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = 0
        self.scripts = 0
    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self.links += 1
        elif tag == "script":
            self.scripts += 1

def main():
    start = time.monotonic()
    try:
        config = json.loads(os.environ["MONITOR_CONFIG"])
        url = config["target_url"]
        if not url.startswith("https://"):
            raise ValueError()
        req = urllib.request.Request(url, headers={"User-Agent": "ResourceMonitor/0.1"})
        with urllib.request.urlopen(req, timeout=20) as response:
            body = response.read(4000001)
            if len(body) > 4000000:
                raise ValueError()
            status = response.status
        elapsed = time.monotonic() - start
        parser = Probe()
        parser.feed(body.decode("utf-8", errors="replace"))
        print(f"status={status} fetch_seconds={elapsed:.2f}")
        print(f"bytes={len(body)} links={parser.links} scripts={parser.scripts}")
        print(f"total_seconds={time.monotonic()-start:.2f}")
        return 0
    except Exception:
        print(f"probe_failed elapsed_seconds={time.monotonic()-start:.2f}")
        return 1

if __name__ == "__main__":
    raise SystemExit(main())
