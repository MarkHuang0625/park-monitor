#!/usr/bin/env python3
"""
Theme park performance monitor: Reddit sentiment + live wait times + historical trends.

Usage:
  python park_monitor.py --refresh          # collect data and rebuild dashboard
  python park_monitor.py --refresh --demo   # include demo Reddit if live fetch fails
  python park_monitor.py --export-only      # rebuild dashboard from cached data
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

try:
    import requests
except ImportError:
    import subprocess

    subprocess.check_call([sys.executable, "-m", "pip", "install", "requests", "-q"])
    import requests

BASE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE, "data")
CONFIG_PATH = os.path.join(BASE, "config.json")
HISTORY_PATH = os.path.join(DATA_DIR, "wait_history.jsonl")
REDDIT_PATH = os.path.join(DATA_DIR, "reddit_posts.jsonl")
THRILL_SNAPSHOT_PATH = os.path.join(DATA_DIR, "thrill_data_snapshot.json")
DASHBOARD_HTML = os.path.join(BASE, "dashboard.html")
DASHBOARD_JSON = os.path.join(BASE, "dashboard_data.json")
DASHBOARD_MD = os.path.join(BASE, "dashboard_summary.md")

USER_AGENT = "park-monitor/1.0 (public-data research dashboard)"
THRILL_WAITS_URL = "https://www.thrill-data.com/waits/"
QUEUE_TIMES_ATTR = "Powered by Queue-Times.com — https://queue-times.com/"

# Weekly wait change scraped from Thrill Data /waits/ (Jun 2026 snapshot).
# Refreshed when fetch succeeds; used as fallback when blocked.
THRILL_WEEKLY_FALLBACK: dict[str, dict[str, Any]] = {
    "Six Flags Great America": {"avg_wait_min": 8, "wow_pct": -8.3},
    "Six Flags Magic Mountain": {"avg_wait_min": 8, "wow_pct": 0.0},
    "Six Flags Great Adventure": {"avg_wait_min": 8, "wow_pct": -36.7},
    "Six Flags Over Texas": {"avg_wait_min": 15, "wow_pct": -4.5},
    "Six Flags Fiesta Texas": {"avg_wait_min": 9, "wow_pct": -13.3},
    "Six Flags Over Georgia": {"avg_wait_min": 4, "wow_pct": -46.2},
    "Six Flags New England": {"avg_wait_min": 6, "wow_pct": 0.0},
    "Six Flags Discovery Kingdom": {"avg_wait_min": 9, "wow_pct": -35.3},
    "La Ronde": {"avg_wait_min": 22, "wow_pct": -22.2},
    "Cedar Point": {"avg_wait_min": 16, "wow_pct": 16.7},
    "Kings Island": {"avg_wait_min": 13, "wow_pct": None},
    "Canada's Wonderland": {"avg_wait_min": 14, "wow_pct": 8.0},
    "California's Great America": {"avg_wait_min": 24, "wow_pct": -7.1},
    "Carowinds": {"avg_wait_min": 5, "wow_pct": 0.0},
    "Kings Dominion": {"avg_wait_min": 4, "wow_pct": 0.0},
    "Dorney Park": {"avg_wait_min": 6, "wow_pct": -15.8},
}


def load_config() -> dict:
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return json.load(f)


def ensure_data_dir() -> None:
    os.makedirs(DATA_DIR, exist_ok=True)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def load_jsonl(path: str) -> list[dict]:
    if not os.path.isfile(path):
        return []
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def append_jsonl(path: str, row: dict) -> None:
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


# ── Reddit ──────────────────────────────────────────────────────────────────


def fetch_reddit_pullpush(subreddit: str, days: int, limit: int) -> tuple[list[dict], str]:
    after = int((datetime.now(timezone.utc) - timedelta(days=days)).timestamp())
    url = "https://api.pullpush.io/reddit/search/submission/"
    params = {
        "subreddit": subreddit,
        "after": after,
        "size": min(limit, 100),
        "sort": "desc",
        "sort_type": "created_utc",
    }
    r = requests.get(url, params=params, headers={"User-Agent": USER_AGENT}, timeout=45)
    r.raise_for_status()
    data = r.json().get("data", [])
    if data:
        return data, "pullpush"

    r2 = requests.get(
        url,
        params={"subreddit": subreddit, "size": min(limit, 100), "sort": "desc", "sort_type": "created_utc"},
        headers={"User-Agent": USER_AGENT},
        timeout=45,
    )
    r2.raise_for_status()
    all_posts = r2.json().get("data", [])
    recent = [p for p in all_posts if (p.get("created_utc") or 0) >= after]
    if recent:
        return recent, "pullpush-recent"
    if all_posts:
        return all_posts[:limit], "pullpush-archive"
    return [], "pullpush-empty"


def fetch_reddit_native(subreddit: str, limit: int) -> list[dict]:
    url = f"https://www.reddit.com/r/{subreddit}/new.json"
    params = {"limit": min(limit, 100)}
    r = requests.get(
        url,
        params=params,
        headers={"User-Agent": USER_AGENT},
        timeout=20,
    )
    r.raise_for_status()
    children = r.json().get("data", {}).get("children", [])
    out = []
    for c in children:
        d = c.get("data", {})
        out.append(
            {
                "title": d.get("title", ""),
                "selftext": d.get("selftext", ""),
                "author": d.get("author", ""),
                "score": d.get("score", 0),
                "num_comments": d.get("num_comments", 0),
                "created_utc": d.get("created_utc"),
                "permalink": d.get("permalink", ""),
                "url": d.get("url", ""),
                "subreddit": d.get("subreddit", subreddit),
            }
        )
    return out


def normalize_reddit_post(raw: dict, source: str) -> dict:
    created = raw.get("created_utc") or raw.get("created")
    if isinstance(created, (int, float)):
        created_iso = datetime.fromtimestamp(created, tz=timezone.utc).isoformat()
    else:
        created_iso = utc_now_iso()
    permalink = raw.get("permalink") or ""
    if permalink and not permalink.startswith("http"):
        permalink = f"https://www.reddit.com{permalink}"
    text = " ".join(
        filter(
            None,
            [
                raw.get("title", ""),
                raw.get("selftext", ""),
            ],
        )
    )
    return {
        "id": raw.get("id") or raw.get("name") or permalink,
        "title": raw.get("title", ""),
        "body": raw.get("selftext", ""),
        "text": text,
        "author": raw.get("author", ""),
        "score": raw.get("score", 0),
        "num_comments": raw.get("num_comments", 0),
        "created_utc": created_iso,
        "permalink": permalink,
        "subreddit": raw.get("subreddit", ""),
        "source": source,
    }


def demo_reddit_posts() -> list[dict]:
    samples = [
        {
            "title": "Great America today - Superman 90 min wait, Joker closed all day",
            "selftext": "Flash pass line was moving faster but ops felt understaffed. Bathrooms near Gotham were dirty.",
            "author": "demo_user1",
            "score": 42,
            "num_comments": 18,
            "created_utc": time.time() - 86400,
            "permalink": "/r/sixflagsgreatamerica/comments/demo1/",
            "subreddit": "sixflagsgreatamerica",
        },
        {
            "title": "Cedar Point opening day crowds - Maverick 120 min standby",
            "selftext": "Park was packed. Steel Vengeance broke down twice. Food lines were insane.",
            "author": "demo_user2",
            "score": 88,
            "num_comments": 45,
            "created_utc": time.time() - 172800,
            "permalink": "/r/cedarpoint/comments/demo2/",
            "subreddit": "cedarpoint",
        },
        {
            "title": "Magic Mountain walk-ons before noon then everything blew up",
            "selftext": "Tatsu and X2 both 60+ by 1pm. Single train ops on Twisted Colossus.",
            "author": "demo_user3",
            "score": 31,
            "num_comments": 12,
            "created_utc": time.time() - 259200,
            "permalink": "/r/SixFlagsMagicMountain/comments/demo3/",
            "subreddit": "SixFlagsMagicMountain",
        },
    ]
    return [normalize_reddit_post(s, "demo") for s in samples]


def collect_reddit(cfg: dict, use_demo: bool) -> tuple[list[dict], list[str]]:
    seen_ids: set[str] = set()
    posts: list[dict] = []
    notes: list[str] = []
    subs = list(dict.fromkeys(cfg.get("reddit_subreddits", [])))
    days = cfg.get("reddit_lookback_days", 14)
    limit = cfg.get("reddit_post_limit_per_sub", 25)

    for sub in subs:
        fetched: list[dict] = []
        try:
            raw = fetch_reddit_native(sub, limit)
            fetched = [normalize_reddit_post(r, "reddit") for r in raw]
            notes.append(f"r/{sub}: {len(fetched)} posts (Reddit JSON)")
        except Exception as e1:
            try:
                raw, mode = fetch_reddit_pullpush(sub, days, limit)
                fetched = [normalize_reddit_post(r, mode) for r in raw]
                lag = " (archive; may not include latest posts)" if mode == "pullpush-archive" else ""
                notes.append(f"r/{sub}: {len(fetched)} posts ({mode}){lag}")
            except Exception as e2:
                notes.append(f"r/{sub}: failed ({e1}; {e2})")

        for p in fetched:
            pid = str(p.get("id", ""))
            if pid and pid not in seen_ids:
                seen_ids.add(pid)
                posts.append(p)

    if not posts and use_demo:
        posts = demo_reddit_posts()
        notes.append("Using demo Reddit posts (live fetch unavailable)")

    posts.sort(key=lambda p: p.get("created_utc", ""), reverse=True)

    with open(REDDIT_PATH, "w", encoding="utf-8") as f:
        for p in posts:
            f.write(json.dumps(p, ensure_ascii=False) + "\n")

    return posts, notes


# ── KPI classification ────────────────────────────────────────────────────


def match_park(text: str, parks: list[dict]) -> str | None:
    t = text.lower()
    best_id = None
    best_len = 0
    for park in parks:
        for alias in park.get("aliases", []) + [park["name"].lower()]:
            if alias in t and len(alias) > best_len:
                best_id = park["id"]
                best_len = len(alias)
    return best_id


def classify_kpis(text: str, kpi_patterns: dict) -> list[str]:
    t = text.lower()
    hits = []
    for key, meta in kpi_patterns.items():
        for kw in meta.get("keywords", []):
            if kw.lower() in t:
                hits.append(key)
                break
    return hits


def analyze_reddit(posts: list[dict], cfg: dict) -> dict:
    parks = cfg["parks"]
    patterns = cfg["kpi_patterns"]
    by_park: dict[str, list[dict]] = defaultdict(list)
    unassigned: list[dict] = []
    kpi_counts: Counter = Counter()
    park_kpi: dict[str, Counter] = defaultdict(Counter)

    for post in posts:
        text = post.get("text", "")
        park_id = match_park(text, parks)
        if not park_id:
            for sub in post.get("subreddit", "").lower().replace("_", ""):
                pass
            for park in parks:
                for sr in park.get("subreddits", []):
                    if sr.lower() == post.get("subreddit", "").lower():
                        park_id = park["id"]
                        break
                if park_id:
                    break
        kpis = classify_kpis(text, patterns)
        enriched = {**post, "park_id": park_id, "kpis": kpis}
        if park_id:
            by_park[park_id].append(enriched)
        else:
            unassigned.append(enriched)
        for k in kpis:
            kpi_counts[k] += 1
            if park_id:
                park_kpi[park_id][k] += 1

    return {
        "by_park": dict(by_park),
        "unassigned": unassigned,
        "kpi_counts": dict(kpi_counts),
        "park_kpi": {k: dict(v) for k, v in park_kpi.items()},
    }


# ── Wait times (Queue-Times.com) ────────────────────────────────────────────


def fetch_park_waits(park: dict) -> dict:
    pid = park["queue_times_id"]
    url = f"https://queue-times.com/parks/{pid}/queue_times.json"
    r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=30)
    r.raise_for_status()
    data = r.json()
    rides = []
    for land in data.get("lands", []):
        for ride in land.get("rides", []):
            rides.append(
                {
                    "name": ride.get("name"),
                    "is_open": ride.get("is_open"),
                    "wait_time": ride.get("wait_time") or 0,
                    "last_updated": ride.get("last_updated"),
                }
            )
    for ride in data.get("rides", []):
        rides.append(
            {
                "name": ride.get("name"),
                "is_open": ride.get("is_open"),
                "wait_time": ride.get("wait_time") or 0,
                "last_updated": ride.get("last_updated"),
            }
        )

    open_rides = [x for x in rides if x.get("is_open")]
    waits = [x["wait_time"] for x in open_rides if x["wait_time"] > 0]
    closed = [x["name"] for x in rides if not x.get("is_open")]
    top_waits = sorted(open_rides, key=lambda x: x["wait_time"], reverse=True)[:8]
    now_utc = datetime.now(timezone.utc)
    last_updates = []
    for r in rides:
        lu = r.get("last_updated")
        if lu:
            try:
                ts = datetime.fromisoformat(str(lu).replace("Z", "+00:00"))
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                last_updates.append(ts)
            except Exception:
                pass
    data_age_minutes = int((now_utc - max(last_updates)).total_seconds() / 60) if last_updates else None
    park_is_open = len(open_rides) > 0 and (data_age_minutes is None or data_age_minutes < 180)

    snapshot = {
        "park_id": park["id"],
        "park_name": park["name"],
        "chain": park["chain"],
        "ts": utc_now_iso(),
        "ride_count": len(rides),
        "open_count": len(open_rides),
        "closed_count": len(closed),
        "avg_wait_min": round(sum(waits) / len(waits), 1) if waits else 0,
        "max_wait_min": max(waits) if waits else 0,
        "median_wait_min": sorted(waits)[len(waits) // 2] if waits else 0,
        "top_waits": top_waits,
        "closed_rides": closed[:15],
        "source_url": url,
        "attribution": QUEUE_TIMES_ATTR,        "park_is_open": park_is_open,        "data_age_minutes": data_age_minutes,        "park_is_open": park_is_open,
    }
    return snapshot


def collect_waits(cfg: dict) -> tuple[list[dict], list[str]]:
    snapshots = []
    notes = []
    for park in cfg["parks"]:
        try:
            snap = fetch_park_waits(park)
            snapshots.append(snap)
            notes.append(f"{park['name']}: avg {snap['avg_wait_min']} min ({snap['open_count']} open)")
        except Exception as e:
            notes.append(f"{park['name']}: wait fetch failed ({e})")
    return snapshots, notes


def record_wait_history(snapshots: list[dict]) -> None:
    today = datetime.now(timezone.utc).date().isoformat()
    existing = load_jsonl(HISTORY_PATH)
    if existing and existing[-1].get("date") == today:
        return
    row = {
        "date": today,
        "ts": utc_now_iso(),
        "parks": {
            s["park_id"]: {
                "avg_wait_min": s["avg_wait_min"],
                "open_count": s["open_count"],
                "closed_count": s["closed_count"],
                "max_wait_min": s["max_wait_min"],
            }
            for s in snapshots
        },
    }
    append_jsonl(HISTORY_PATH, row)


def compute_yoy(history: list[dict], park_id: str) -> dict | None:
    if not history:
        return None
    latest = history[-1]
    cur = latest.get("parks", {}).get(park_id)
    if not cur:
        return None
    target_date = (
        datetime.fromisoformat(latest["date"]).date() - timedelta(days=365)
    ).isoformat()
    prior = None
    for row in reversed(history[:-1]):
        d = row.get("date")
        if d and d <= target_date:
            prior = row.get("parks", {}).get(park_id)
            prior_date = d
            break
    if not prior or not prior.get("avg_wait_min"):
        return {"available": False, "reason": "Need ~365 days of daily snapshots"}
    cur_avg = cur["avg_wait_min"]
    prior_avg = prior["avg_wait_min"]
    if prior_avg == 0:
        return {"available": False, "reason": "Prior-year baseline was zero"}
    yoy_pct = round((cur_avg - prior_avg) / prior_avg * 100, 1)
    return {
        "available": True,
        "current_avg": cur_avg,
        "prior_avg": prior_avg,
        "prior_date": prior_date,
        "yoy_pct": yoy_pct,
    }


def compute_wow(history: list[dict], park_id: str) -> dict | None:
    if len(history) < 2:
        return None
    cur = history[-1].get("parks", {}).get(park_id)
    prev = history[-2].get("parks", {}).get(park_id)
    if not cur or not prev or not prev.get("avg_wait_min"):
        return None
    pct = round((cur["avg_wait_min"] - prev["avg_wait_min"]) / prev["avg_wait_min"] * 100, 1)
    return {"wow_pct": pct, "current": cur["avg_wait_min"], "prior": prev["avg_wait_min"]}


def build_wait_trends(history: list[dict], park_id: str, days: int = 90) -> list[dict]:
    cutoff = (datetime.now(timezone.utc).date() - timedelta(days=days)).isoformat()
    points = []
    for row in history:
        if row.get("date", "") >= cutoff:
            p = row.get("parks", {}).get(park_id)
            if p:
                points.append({"date": row["date"], "avg_wait_min": p["avg_wait_min"]})
    return points


# ── Thrill Data ─────────────────────────────────────────────────────────────


def parse_thrill_waits_page(html: str) -> dict[str, dict]:
    """Parse park name, avg wait, and weekly % change from Thrill Data /waits/ HTML/text."""
    out: dict[str, dict] = {}
    chunks = re.split(r"Waits at ", html)
    for chunk in chunks[1:]:
        m_name = re.match(r"([^\n]+)\n", chunk)
        if not m_name:
            continue
        name = m_name.group(1).strip()
        avg = None
        m_avg = re.search(r"(\d+)\s+MIN", chunk[:120])
        if m_avg:
            avg = int(m_avg.group(1))
        wow = None
        m_wow = re.search(r"Change in Wait Times This Week:\s*\n\s*([+-]?\d+\.?\d*)%", chunk)
        if m_wow:
            wow = float(m_wow.group(1))
        out[name] = {"avg_wait_min": avg, "wow_pct": wow}
    return out


def fetch_thrill_snapshot(parks: list[dict]) -> tuple[dict, list[str]]:
    notes = []
    parsed: dict[str, dict] = {}
    try:
        r = requests.get(
            THRILL_WAITS_URL,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Accept": "text/html",
            },
            timeout=30,
        )
        if r.ok:
            parsed = parse_thrill_waits_page(r.text)
            notes.append(f"Thrill Data: parsed {len(parsed)} park blocks")
        else:
            notes.append(f"Thrill Data: HTTP {r.status_code}")
    except Exception as e:
        notes.append(f"Thrill Data fetch failed: {e}")

    if not parsed:
        parsed = dict(THRILL_WEEKLY_FALLBACK)
        notes.append("Using embedded Thrill Data weekly snapshot")

    by_park: dict[str, dict] = {}
    for park in parks:
        td_name = park.get("thrill_data_name", park["name"])
        short = park["name"].replace("Six Flags ", "")
        # Prefer exact Thrill Data graph labels, then short names — avoid fuzzy false matches
        row = (
            parsed.get(td_name)
            or parsed.get(short)
            or parsed.get(park["name"])
        )
        if not row and short:
            for k, v in parsed.items():
                if k.lower() == short.lower() or k.lower().startswith(short.lower()):
                    row = v
                    break
        by_park[park["id"]] = {
            **(row or {}),
            "thrill_data_name": td_name,
            "thrill_waits_url": THRILL_WAITS_URL,
            "thrill_graph_url": "https://www.thrill-data.com/graph",
            "source": "thrill-data.com",
        }

    snapshot = {"ts": utc_now_iso(), "parks": by_park, "notes": notes}
    with open(THRILL_SNAPSHOT_PATH, "w", encoding="utf-8") as f:
        json.dump(snapshot, f, ensure_ascii=False, indent=2)
    return snapshot, notes


# ── Dashboard assembly ──────────────────────────────────────────────────────


def park_lookup(cfg: dict) -> dict[str, dict]:
    return {p["id"]: p for p in cfg["parks"]}


def build_dashboard_payload(
    cfg: dict,
    reddit_posts: list[dict],
    reddit_analysis: dict,
    wait_snapshots: list[dict],
    thrill: dict,
    history: list[dict],
    notes: list[str],
) -> dict:
    waits_by_id = {s["park_id"]: s for s in wait_snapshots}
    thrill_by_id = thrill.get("parks", {})
    parks_out = []

    for park in cfg["parks"]:
        pid = park["id"]
        wait = waits_by_id.get(pid, {})
        td = thrill_by_id.get(pid, {})
        yoy = compute_yoy(history, pid)
        wow_local = compute_wow(history, pid)
        trend = build_wait_trends(history, pid)
        posts = reddit_analysis["by_park"].get(pid, [])
        park_kpis = reddit_analysis["park_kpi"].get(pid, {})

        parks_out.append(
            {
                "id": pid,
                "name": park["name"],
                "chain": park["chain"],
                "queue_times_id": park["queue_times_id"],
                "thrill_data_name": park.get("thrill_data_name"),
                "live_waits": wait,
                "thrill_data": td,
                "yoy": yoy,
                "wow_local": wow_local,
                "trend_90d": trend,
                "reddit_posts": posts[:20],
                "reddit_count": len(posts),
                "kpi_counts": park_kpis,
                "sources": {
                    "queue_times": wait.get("source_url"),
                    "thrill_data_waits": THRILL_WAITS_URL,
                    "thrill_data_graph": "https://www.thrill-data.com/graph",
                    "reddit_cache": REDDIT_PATH,
                },
            }
        )

    chain_summary = defaultdict(lambda: {"parks": 0, "avg_wait": [], "reddit_posts": 0, "kpis": Counter()})
    for p in parks_out:
        c = p["chain"]
        chain_summary[c]["parks"] += 1
        if p["live_waits"].get("avg_wait_min") is not None:
            chain_summary[c]["avg_wait"].append(p["live_waits"]["avg_wait_min"])
        chain_summary[c]["reddit_posts"] += p["reddit_count"]
        chain_summary[c]["kpis"].update(p["kpi_counts"])

    chains = {}
    for chain, data in chain_summary.items():
        avgs = data["avg_wait"]
        chains[chain] = {
            "park_count": data["parks"],
            "avg_wait_min": round(sum(avgs) / len(avgs), 1) if avgs else 0,
            "reddit_posts": data["reddit_posts"],
            "kpi_counts": dict(data["kpis"]),
        }

    return {
        "generated_at": utc_now_iso(),
        "notes": notes,
        "attributions": {
            "wait_times_live": QUEUE_TIMES_ATTR,
            "wait_times_historical": "Thrill Data (thrill-data.com/waits/) — weekly averages; y/y from local snapshots",
            "reddit": "PullPush.io archive + Reddit public JSON",
        },
        "kpi_patterns": cfg["kpi_patterns"],
        "chains": chains,
        "parks": parks_out,
        "reddit_summary": {
            "total_posts": len(reddit_posts),
            "kpi_counts": reddit_analysis["kpi_counts"],
            "unassigned_count": len(reddit_analysis["unassigned"]),
        },
        "history_days": len(history),
        "data_files": {
            "wait_history": HISTORY_PATH,
            "reddit_posts": REDDIT_PATH,
            "thrill_snapshot": THRILL_SNAPSHOT_PATH,
        },
    }


def build_markdown(payload: dict) -> str:
    lines = [
        "# Theme Park Performance Monitor",
        "",
        f"**Updated:** {payload['generated_at']}",
        "",
        "## Chain summary",
        "",
        "| Chain | Parks | Avg wait (live) | Reddit posts (14d) | Top KPI signals |",
        "| --- | ---: | ---: | ---: | --- |",
    ]
    kpi_labels = {k: v["label"] for k, v in payload["kpi_patterns"].items()}
    for chain, data in payload["chains"].items():
        top_kpis = sorted(data["kpi_counts"].items(), key=lambda x: -x[1])[:3]
        kpi_str = ", ".join(f"{kpi_labels.get(k, k)} ({n})" for k, n in top_kpis) or "—"
        lines.append(
            f"| {chain} | {data['park_count']} | {data['avg_wait_min']} min | {data['reddit_posts']} | {kpi_str} |"
        )

    lines.extend(["", "## Parks", ""])
    for p in sorted(payload["parks"], key=lambda x: (x["chain"], x["name"])):
        td = p.get("thrill_data") or {}
        wow = td.get("wow_pct")
        wow_s = f"{wow:+.1f}%" if wow is not None else "n/a"
        yoy = p.get("yoy") or {}
        yoy_s = f"{yoy['yoy_pct']:+.1f}%" if yoy.get("available") else "collecting history"
        lines.append(f"### {p['name']} ({p['chain']})")
        lines.append(
            f"- Live avg wait: **{p['live_waits'].get('avg_wait_min', '—')} min** "
            f"({p['live_waits'].get('open_count', 0)} rides open)"
        )
        lines.append(f"- Thrill Data weekly Δ: **{wow_s}** (avg {td.get('avg_wait_min', '—')} min on thrill-data.com)")
        lines.append(f"- Local y/y trend: **{yoy_s}**")
        if p["kpi_counts"]:
            flags = ", ".join(f"{kpi_labels.get(k, k)} ×{n}" for k, n in sorted(p["kpi_counts"].items(), key=lambda x: -x[1]))
            lines.append(f"- Reddit KPI flags: {flags}")
        lines.append(f"- Sources: [Queue-Times]({p['sources']['queue_times']}) · [Thrill Data waits]({THRILL_WAITS_URL})")
        lines.append("")

    lines.extend(["## Data files", ""])
    for k, v in payload["data_files"].items():
        lines.append(f"- `{k}`: `{v}`")
    return "\n".join(lines)


def build_html(payload: dict) -> str:
    data_json = json.dumps(payload, ensure_ascii=False)
    kpi_labels = {k: v["label"] for k, v in payload["kpi_patterns"].items()}
    kpi_icons = {k: v.get("icon", "•") for k, v in payload["kpi_patterns"].items()}

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Park Performance Monitor</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
<style>
:root {{
  --bg: #0f1419;
  --panel: #1a2332;
  --panel2: #243044;
  --text: #e8eef7;
  --muted: #8fa3bf;
  --accent: #4dabf7;
  --green: #51cf66;
  --red: #ff6b6b;
  --amber: #ffd43b;
  --border: #2d3a4f;
}}
* {{ box-sizing: border-box; }}
body {{
  margin: 0;
  font-family: "Segoe UI", system-ui, -apple-system, sans-serif;
  background: var(--bg);
  color: var(--text);
  line-height: 1.45;
}}
header {{
  padding: 1.25rem 1.5rem;
  border-bottom: 1px solid var(--border);
  background: linear-gradient(135deg, #152238 0%, #0f1419 100%);
}}
header h1 {{ margin: 0 0 .25rem; font-size: 1.5rem; font-weight: 600; }}
header p {{ margin: 0; color: var(--muted); font-size: .9rem; }}
.wrap {{ max-width: 1400px; margin: 0 auto; padding: 1rem 1.5rem 3rem; }}
.grid {{ display: grid; gap: 1rem; }}
.grid-2 {{ grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); }}
.grid-3 {{ grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); }}
.card {{
  background: var(--panel);
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 1rem 1.1rem;
}}
.card h2, .card h3 {{ margin: 0 0 .75rem; font-size: 1rem; font-weight: 600; }}
.stat {{ font-size: 1.75rem; font-weight: 700; }}
.muted {{ color: var(--muted); font-size: .85rem; }}
.pill {{
  display: inline-block;
  padding: .15rem .55rem;
  border-radius: 999px;
  font-size: .75rem;
  background: var(--panel2);
  border: 1px solid var(--border);
  margin: .15rem .25rem .15rem 0;
}}
.pill.neg {{ color: var(--green); border-color: #2f6b3a; }}
.pill.pos {{ color: var(--red); border-color: #6b2f2f; }}
.pill.neu {{ color: var(--amber); }}
.toolbar {{
  display: flex; flex-wrap: wrap; gap: .5rem; align-items: center; margin-bottom: 1rem;
}}
select, button, input {{
  background: var(--panel2);
  color: var(--text);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: .45rem .65rem;
  font-size: .9rem;
}}
button {{ cursor: pointer; }}
button:hover {{ border-color: var(--accent); }}
table {{ width: 100%; border-collapse: collapse; font-size: .85rem; }}
th, td {{ text-align: left; padding: .45rem .35rem; border-bottom: 1px solid var(--border); }}
th {{ color: var(--muted); font-weight: 500; }}
tr:hover td {{ background: rgba(255,255,255,.02); }}
a {{ color: var(--accent); text-decoration: none; }}
a:hover {{ text-decoration: underline; }}
.post {{
  border-left: 3px solid var(--border);
  padding: .5rem .75rem;
  margin: .5rem 0;
  background: rgba(0,0,0,.15);
  border-radius: 0 8px 8px 0;
}}
.post .title {{ font-weight: 600; margin-bottom: .25rem; }}
.kpi-tag {{ font-size: .72rem; opacity: .9; }}
.chain-six {{ border-top: 3px solid #e03131; }}
.chain-cedar {{ border-top: 3px solid #339af0; }}
.chart-box {{ height: 280px; position: relative; }}
#parkDetail {{ display: none; }}
footer {{ margin-top: 2rem; color: var(--muted); font-size: .8rem; }}
</style>
</head>
<body>
<header>
  <h1>🎢 Theme Park Performance Monitor</h1>
  <p>Six Flags & Cedar Fair · Reddit soft KPIs · Live waits · Thrill Data trends · <span id="updatedAt"></span></p>
</header>
<div class="wrap">
  <div class="toolbar">
    <label>Chain <select id="chainFilter"><option value="">All chains</option></select></label>
    <label>Park <select id="parkSelect"></select></label>
    <button id="exportJson">Export JSON</button>
    <button id="showSources">Data sources</button>
  </div>

  <div class="grid grid-3" id="chainCards"></div>

  <div class="grid grid-2" style="margin-top:1rem">
    <div class="card">
      <h2>Wait times — chain comparison (live)</h2>
      <div class="chart-box"><canvas id="chainWaitChart"></canvas></div>
      <p class="muted">{payload['attributions']['wait_times_live']}</p>
    </div>
    <div class="card">
      <h2>Reddit KPI signals (all parks)</h2>
      <div class="chart-box"><canvas id="kpiChart"></canvas></div>
    </div>
  </div>

  <div class="card" style="margin-top:1rem">
    <h2>Park overview</h2>
    <table id="parkTable">
      <thead>
        <tr>
          <th>Park</th><th>Chain</th><th>Live avg</th><th>Open</th><th>Closed</th>
          <th>Thrill Data WoW</th><th>Local WoW</th><th>Local YoY</th><th>Reddit</th><th>KPI flags</th>
        </tr>
      </thead>
      <tbody></tbody>
    </table>
  </div>

  <div class="card" id="parkDetail" style="margin-top:1rem">
    <h2 id="detailTitle">Park detail</h2>
    <div class="grid grid-2">
      <div>
        <h3>Wait trend (local snapshots, 90d)</h3>
        <div class="chart-box"><canvas id="trendChart"></canvas></div>
        <p class="muted">Daily snapshots build y/y once ~365 days collected. Thrill Data historical graphs: <a href="https://www.thrill-data.com/graph" target="_blank">thrill-data.com/graph</a></p>
      </div>
      <div>
        <h3>Top live ride waits</h3>
        <div id="topWaits"></div>
        <h3 style="margin-top:1rem">Closed rides</h3>
        <div id="closedRides" class="muted"></div>
      </div>
    </div>
    <h3 style="margin-top:1rem">Recent Reddit mentions</h3>
    <div id="redditList"></div>
  </div>

  <div class="card" id="sourcesPanel" style="margin-top:1rem; display:none">
    <h2>Underlying data sources</h2>
    <ul id="sourcesList"></ul>
    <p class="muted">Refresh: <code>python park_monitor.py --refresh</code> · Claude reads <code>dashboard_data.json</code></p>
  </div>

  <footer>
    <p>Public-data research dashboard. Not affiliated with Six Flags, Cedar Fair, Reddit, Queue-Times, or Thrill Data.</p>
  </footer>
</div>

<script>
const DATA = {data_json};
const KPI_LABELS = {json.dumps(kpi_labels)};
const KPI_ICONS = {json.dumps(kpi_icons)};

document.getElementById('updatedAt').textContent = new Date(DATA.generated_at).toLocaleString('en-US', {{timeZone: 'America/New_York', year: 'numeric', month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit', second: '2-digit'}}) + ' ET';

let trendChart = null;
let chainChart = null;
let kpiChart = null;

function pctPill(val, invert=false) {{
  if (val === null || val === undefined) return '<span class="pill">n/a</span>';
  const good = invert ? val < 0 : val > 0;
  const bad = invert ? val > 0 : val < 0;
  const cls = val === 0 ? 'neu' : (bad ? 'pos' : 'good' ? 'neg' : 'neu');
  const sign = val > 0 ? '+' : '';
  return `<span class="pill ${{cls}}">${{sign}}${{val}}%</span>`;
}}

function kpiTags(counts) {{
  return Object.entries(counts || {{}}).sort((a,b)=>b[1]-a[1]).map(([k,n]) =>
    `<span class="pill kpi-tag">${{KPI_ICONS[k]||'•'}} ${{KPI_LABELS[k]||k}} (${{n}})</span>`
  ).join('');
}}

function initFilters() {{
  const chains = [...new Set(DATA.parks.map(p => p.chain))];
  const cf = document.getElementById('chainFilter');
  chains.forEach(c => {{ const o = document.createElement('option'); o.value = c; o.textContent = c; cf.appendChild(o); }});
  cf.addEventListener('change', renderTable);
  const ps = document.getElementById('parkSelect');
  DATA.parks.forEach(p => {{ const o = document.createElement('option'); o.value = p.id; o.textContent = p.name; ps.appendChild(o); }});
  ps.addEventListener('change', () => showPark(ps.value));
  if (DATA.parks.length) showPark(DATA.parks[0].id);
}}

function filteredParks() {{
  const chain = document.getElementById('chainFilter').value;
  return DATA.parks.filter(p => !chain || p.chain === chain);
}}

function renderChainCards() {{
  const el = document.getElementById('chainCards');
  el.innerHTML = '';
  Object.entries(DATA.chains).forEach(([chain, d]) => {{
    const div = document.createElement('div');
    div.className = 'card ' + (chain.includes('Six') ? 'chain-six' : 'chain-cedar');
    const top = Object.entries(d.kpi_counts||{{}}).sort((a,b)=>b[1]-a[1]).slice(0,3);
    div.innerHTML = `<h3>${{chain}}</h3>
      <div class="stat">${{d.avg_wait_min}}<span style="font-size:.9rem;color:var(--muted)"> min avg</span></div>
      <p class="muted">${{d.park_count}} parks · ${{d.reddit_posts}} Reddit posts</p>
      <div>${{top.map(([k,n])=>`<span class="pill">${{KPI_ICONS[k]||''}} ${{KPI_LABELS[k]||k}} ${{n}}</span>`).join('')}}</div>`;
    el.appendChild(div);
  }});
}}

function renderTable() {{
  const tbody = document.querySelector('#parkTable tbody');
  tbody.innerHTML = '';
  filteredParks().forEach(p => {{
    const w = p.live_waits || {{}};
    const td = p.thrill_data || {{}};
    const yoy = p.yoy || {{}};
    const wl = p.wow_local || {{}};
    const tr = document.createElement('tr');
    tr.style.cursor = 'pointer';
    tr.onclick = () => {{ document.getElementById('parkSelect').value = p.id; showPark(p.id); }};
    const parkOpen = w.park_is_open;    const waitCell = parkOpen ? `${{w.avg_wait_min ?? '—'}} min` : `<span class="pill" style="background:#555;color:#ccc">Closed</span>`;
      <td>${{waitCell}}</td><td>${{openCell}}</td><td>${{closedCell}}</td>
      <td>${{pctPill(td.wow_pct, true)}}</td>
      <td>${{pctPill(wl.wow_pct, true)}}</td>
      <td>${{yoy.available ? pctPill(yoy.yoy_pct, true) : '<span class="pill">building</span>'}}</td>
      <td>${{p.reddit_count}}</td><td>${{kpiTags(p.kpi_counts)}}</td>`;
    tbody.appendChild(tr);
  }});
}}

function showPark(id) {{
  const p = DATA.parks.find(x => x.id === id);
  if (!p) return;
  document.getElementById('parkDetail').style.display = 'block';
  document.getElementById('detailTitle').textContent = p.name + ' (' + p.chain + ')';
  const w = p.live_waits || {{}};
  document.getElementById('topWaits').innerHTML = (w.top_waits||[]).map(r =>
    `<div>${{r.name}} — <strong>${{r.wait_time}} min</strong></div>`).join('') || '<span class="muted">No open rides with waits</span>';
  document.getElementById('closedRides').textContent = (w.closed_rides||[]).join(', ') || 'None reported';
  document.getElementById('redditList').innerHTML = (p.reddit_posts||[]).map(post => `
    <div class="post">
      <div class="title"><a href="${{post.permalink}}" target="_blank">${{post.title}}</a></div>
      <div class="muted">${{post.created_utc?.slice(0,10)}} · r/${{post.subreddit}} · score ${{post.score}}</div>
      <div>${{kpiTags(Object.fromEntries((post.kpis||[]).map(k=>[k,1])))}}</div>
    </div>`).join('') || '<p class="muted">No recent posts matched this park.</p>';

  const pts = p.trend_90d || [];
  const ctx = document.getElementById('trendChart');
  if (trendChart) trendChart.destroy();
  trendChart = new Chart(ctx, {{
    type: 'line',
    data: {{ labels: pts.map(x=>x.date), datasets: [{{ label: 'Avg wait (min)', data: pts.map(x=>x.avg_wait_min), borderColor: '#4dabf7', tension: 0.25, fill: false }}] }},
    options: {{ responsive: true, maintainAspectRatio: false, plugins: {{ legend: {{ display: false }} }}, scales: {{ x: {{ ticks: {{ color: '#8fa3bf', maxTicksLimit: 8 }} }}, y: {{ ticks: {{ color: '#8fa3bf' }}, beginAtZero: true }} }} }}
  }});
}}

function renderCharts() {{
  const chains = Object.keys(DATA.chains);
  const avgs = chains.map(c => DATA.chains[c].avg_wait_min);
  chainChart = new Chart(document.getElementById('chainWaitChart'), {{
    type: 'bar',
    data: {{ labels: chains, datasets: [{{ label: 'Avg wait (min)', data: avgs, backgroundColor: ['#e03131','#339af0'] }}] }},
    options: {{ responsive: true, maintainAspectRatio: false, plugins: {{ legend: {{ display: false }} }}, scales: {{ x: {{ ticks: {{ color: '#8fa3bf' }} }}, y: {{ ticks: {{ color: '#8fa3bf' }}, beginAtZero: true }} }} }}
  }});

  const kc = DATA.reddit_summary.kpi_counts || {{}};
  const keys = Object.keys(kc);
  kpiChart = new Chart(document.getElementById('kpiChart'), {{
    type: 'doughnut',
    data: {{ labels: keys.map(k => KPI_LABELS[k]||k), datasets: [{{ data: keys.map(k => kc[k]), backgroundColor: ['#ff6b6b','#ffd43b','#51cf66','#4dabf7','#cc5de8','#ff922b','#94d82d','#748ffc'] }}] }},
    options: {{ responsive: true, maintainAspectRatio: false, plugins: {{ legend: {{ position: 'right', labels: {{ color: '#8fa3bf' }} }} }} }}
  }});
}}

document.getElementById('exportJson').onclick = () => {{
  const blob = new Blob([JSON.stringify(DATA, null, 2)], {{type: 'application/json'}});
  const a = document.createElement('a'); a.href = URL.createObjectURL(blob); a.download = 'dashboard_data.json'; a.click();
}};

document.getElementById('showSources').onclick = () => {{
  const p = document.getElementById('sourcesPanel');
  p.style.display = p.style.display === 'none' ? 'block' : 'none';
  document.getElementById('sourcesList').innerHTML = `
    <li><strong>Live waits:</strong> queue-times.com (updated every ~5 min)</li>
    <li><strong>Weekly / historical waits:</strong> <a href="https://www.thrill-data.com/waits/" target="_blank">thrill-data.com/waits</a> + custom graph tool</li>
    <li><strong>Reddit:</strong> PullPush archive + public subreddit JSON → <code>${{DATA.data_files.reddit_posts}}</code></li>
    <li><strong>Local history:</strong> <code>${{DATA.data_files.wait_history}}</code> (${{DATA.history_days}} days)</li>
    <li><strong>Claude:</strong> read <code>dashboard_data.json</code> or run <code>python park_monitor.py --refresh</code></li>`;
}};

initFilters();
renderChainCards();
renderTable();
renderCharts();
</script>
</body>
</html>"""


def refresh(use_demo: bool = False) -> dict:
    ensure_data_dir()
    cfg = load_config()
    notes: list[str] = []

    posts, rnotes = collect_reddit(cfg, use_demo=use_demo)
    notes.extend(rnotes)
    reddit_analysis = analyze_reddit(posts, cfg)

    waits, wnotes = collect_waits(cfg)
    notes.extend(wnotes)
    record_wait_history(waits)

    thrill, tnotes = fetch_thrill_snapshot(cfg["parks"])
    notes.extend(tnotes)

    history = load_jsonl(HISTORY_PATH)
    payload = build_dashboard_payload(cfg, posts, reddit_analysis, waits, thrill, history, notes)

    with open(DASHBOARD_JSON, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    md = build_markdown(payload)
    with open(DASHBOARD_MD, "w", encoding="utf-8") as f:
        f.write(md)

    html = build_html(payload)
    with open(DASHBOARD_HTML, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"Dashboard updated: {DASHBOARD_HTML}")
    print(f"Data export: {DASHBOARD_JSON}")
    print(f"Summary: {DASHBOARD_MD}")
    return payload


def export_only() -> dict:
    cfg = load_config()
    posts = load_jsonl(REDDIT_PATH)
    if not posts:
        posts = demo_reddit_posts()
    reddit_analysis = analyze_reddit(posts, cfg)
    waits = []
    for park in cfg["parks"]:
        try:
            waits.append(fetch_park_waits(park))
        except Exception:
            pass
    thrill = {}
    if os.path.isfile(THRILL_SNAPSHOT_PATH):
        with open(THRILL_SNAPSHOT_PATH, encoding="utf-8") as f:
            thrill = json.load(f)
    else:
        thrill = {"parks": {}, "notes": ["No thrill snapshot"]}
    history = load_jsonl(HISTORY_PATH)
    payload = build_dashboard_payload(cfg, posts, reddit_analysis, waits, thrill, history, ["export-only rebuild"])
    with open(DASHBOARD_JSON, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    with open(DASHBOARD_MD, "w", encoding="utf-8") as f:
        f.write(build_markdown(payload))
    with open(DASHBOARD_HTML, "w", encoding="utf-8") as f:
        f.write(build_html(payload))
    print(f"Rebuilt from cache: {DASHBOARD_HTML}")
    return payload


def main() -> int:
    ap = argparse.ArgumentParser(description="Theme park performance monitor")
    ap.add_argument("--refresh", action="store_true", help="Fetch fresh data and rebuild dashboard")
    ap.add_argument("--export-only", action="store_true", help="Rebuild dashboard from cached JSONL")
    ap.add_argument("--demo", action="store_true", help="Use demo Reddit data if live fetch fails")
    args = ap.parse_args()
    if args.export_only:
        export_only()
    elif args.refresh:
        refresh(use_demo=args.demo)
    else:
        ap.print_help()
        print("\nTip: run with --refresh to collect data and open dashboard.html")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
