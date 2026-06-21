#!/usr/bin/env python3
"""
Build a standalone, fully-offline shareable version of the dashboard.

- Inlines Chart.js so the page needs no internet
- Produces dashboard_share.html (single file, just double-click)
- Zips it (+ data export + readme) into park_monitor_dashboard.zip
"""
from __future__ import annotations

import os
import re
import sys
import urllib.request
import zipfile

BASE = os.path.dirname(os.path.abspath(__file__))
SRC_HTML = os.path.join(BASE, "dashboard.html")
SHARE_HTML = os.path.join(BASE, "dashboard_share.html")
DATA_JSON = os.path.join(BASE, "dashboard_data.json")
SUMMARY_MD = os.path.join(BASE, "dashboard_summary.md")
ZIP_PATH = os.path.join(BASE, "park_monitor_dashboard.zip")

VENDOR_CANDIDATES = [
    os.path.join(BASE, "vendor", "chart.umd.min.js"),
    os.path.join(os.path.dirname(BASE), "logs", "vendor", "chart.umd.min.js"),
]
CDN_TAG_RE = re.compile(
    r'<script src="https://cdn\.jsdelivr\.net/npm/chart\.js[^"]*"></script>'
)
CDN_URL = "https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"


def load_chartjs() -> str:
    for path in VENDOR_CANDIDATES:
        if os.path.isfile(path):
            with open(path, encoding="utf-8") as f:
                print(f"Using local Chart.js: {path}")
                return f.read()
    print("Local Chart.js not found, downloading from CDN...")
    with urllib.request.urlopen(CDN_URL, timeout=30) as r:
        return r.read().decode("utf-8")


def build_share_html() -> str:
    with open(SRC_HTML, encoding="utf-8") as f:
        html = f.read()
    chartjs = load_chartjs()
    inline = f"<script>\n/* Chart.js bundled for offline use */\n{chartjs}\n</script>"
    if CDN_TAG_RE.search(html):
        html = CDN_TAG_RE.sub(lambda _: inline, html, count=1)
    else:
        html = html.replace("</head>", inline + "\n</head>", 1)
    return html


def main() -> int:
    if not os.path.isfile(SRC_HTML):
        print("dashboard.html not found. Run: python park_monitor.py --refresh")
        return 1

    html = build_share_html()
    with open(SHARE_HTML, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"Standalone page: {SHARE_HTML}")

    with zipfile.ZipFile(ZIP_PATH, "w", zipfile.ZIP_DEFLATED) as z:
        z.write(SHARE_HTML, "Park Performance Dashboard.html")
        if os.path.isfile(DATA_JSON):
            z.write(DATA_JSON, "data/dashboard_data.json")
        if os.path.isfile(SUMMARY_MD):
            z.write(SUMMARY_MD, "data/dashboard_summary.md")
        readme = (
            "Theme Park Performance Monitor\n"
            "================================\n\n"
            "HOW TO VIEW:\n"
            "  Double-click 'Park Performance Dashboard.html' to open in any browser.\n"
            "  Works fully offline. No install needed.\n\n"
            "WHAT'S INSIDE:\n"
            "  Park Performance Dashboard.html  - the interactive dashboard (open this)\n"
            "  data/dashboard_data.json         - raw structured data\n"
            "  data/dashboard_summary.md        - text summary\n\n"
            "SOURCES: Reddit (PullPush/Reddit JSON), Queue-Times.com (live waits),\n"
            "Thrill Data (weekly wait trends). Public data only.\n"
        )
        z.writestr("README.txt", readme)

    size_kb = os.path.getsize(ZIP_PATH) / 1024
    print(f"Share package: {ZIP_PATH} ({size_kb:.0f} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
