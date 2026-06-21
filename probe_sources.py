#!/usr/bin/env python3
"""Probe data sources for park monitor."""
import json
import re
import sys

try:
    import requests
except ImportError:
    import subprocess
    subprocess.check_call([sys.executable, "-m", "pip", "install", "requests", "-q"])
    import requests

HEADERS = {
    "User-Agent": "park-monitor/1.0 (research dashboard; contact: local)",
    "Accept": "application/json, text/html, */*",
    "Accept-Language": "en-US,en;q=0.9",
}

session = requests.Session()
session.headers.update(HEADERS)

# queue-times
r = session.get("https://queue-times.com/parks.json", timeout=30)
print("queue-times parks:", r.status_code)
if r.ok:
    for g in r.json():
        if "Six Flags" in g["name"] or "Cedar" in g["name"]:
            print(f"  {g['name']}: {len(g['parks'])} parks")
            for p in g["parks"][:3]:
                print(f"    id={p['id']} {p['name']}")

# thrill-data waits page
r2 = session.get("https://www.thrill-data.com/waits/", timeout=30)
print("thrill-data waits:", r2.status_code, len(r2.text))
if r2.ok:
    links = sorted(set(re.findall(r'href="(/waits/[^"]+)"', r2.text)))
    print("  park links sample:", links[:15])
    # weekly change patterns
    blocks = re.findall(
        r"Waits at ([^\n]+)\n\n(\d+ MIN)?\n\nWaits.*?Change in Wait Times This Week:\n\n([+-]?\d+\.?\d*%)",
        r2.text,
        re.DOTALL,
    )
    print("  parsed blocks:", len(blocks))
    for b in blocks[:5]:
        print("   ", b)

# reddit
r3 = session.get(
    "https://www.reddit.com/r/sixflags/search.json",
    params={"q": "wait time", "restrict_sr": "on", "sort": "new", "limit": 3},
    timeout=30,
)
print("reddit search:", r3.status_code)
if r3.ok:
    posts = r3.json()["data"]["children"]
    print("  posts:", len(posts))
    for p in posts:
        d = p["data"]
        print(f"    {d['title'][:60]}... score={d['score']}")
