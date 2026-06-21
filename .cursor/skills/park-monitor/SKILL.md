---
name: park-monitor
description: >-
  Refresh and analyze the Six Flags / Cedar Fair theme park performance dashboard.
  Use when the user asks about park wait times, Reddit guest sentiment, ride closures,
  crowding, soft KPIs, y/y wait trends, Cedar Fair vs Six Flags comparison, or
  park_monitor dashboard data.
---

# Park Performance Monitor

## Location

Project root: `park_monitor/` (same folder as this skill when installed in-project).

Key outputs:

- `dashboard.html` — interactive UI
- `dashboard_data.json` — structured data for analysis
- `dashboard_summary.md` — markdown summary
- `data/wait_history.jsonl` — daily snapshots for local y/y trends

## Refresh workflow

When the user wants updated data:

```bash
cd park_monitor
python park_monitor.py --refresh
```

If Reddit fetch fails (network timeout), retry with demo fallback:

```bash
python park_monitor.py --refresh --demo
```

Windows shortcut:

```powershell
.\open_dashboard.ps1 -Refresh
```

After refresh, read `dashboard_data.json` for analysis. Open `dashboard.html` if the user wants the visual dashboard.

## Data sources (always cite these)

| Signal | Source | Notes |
| --- | --- | --- |
| Live ride waits | `https://queue-times.com/parks/{id}/queue_times.json` | Updated ~5 min; attribution required |
| Weekly avg / WoW | `https://www.thrill-data.com/waits/` | Embedded snapshot when scrape blocked; link user to `/graph` for custom y/y |
| Reddit posts | PullPush.io + Reddit JSON | Cached in `data/reddit_posts.jsonl` |
| Local y/y | `data/wait_history.jsonl` | Needs daily runs for ~1 year |

## KPI categories

Configured in `config.json` → `kpi_patterns`:

- `long_waits` — queue / standby complaints
- `ride_closure` — down, 102, maintenance, evac
- `crowding` — packed / busy park
- `staffing_ops` — single train, slow ops
- `cleanliness`, `pricing_pass`, `food_service`, `positive_experience`

Posts are matched to parks via aliases in `config.json` → `parks[].aliases` and subreddit mapping.

## Common analysis tasks

**Compare chains:** Use `payload.chains["Six Flags"]` vs `payload.chains["Cedar Fair"]` in `dashboard_data.json`.

**Park deep dive:** Find park by `id` in `payload.parks[]` — includes `live_waits`, `thrill_data.wow_pct`, `yoy`, `reddit_posts`, `kpi_counts`.

**Trend over time:** Use `park.trend_90d` (local snapshots). For Thrill Data historical graphs, direct user to https://www.thrill-data.com/graph and select the park (names match `thrill_data_name` in config).

**Source a claim:** Every park has `sources` with queue-times URL; Reddit posts include `permalink`.

## Do not

- Claim affiliation with Six Flags, Cedar Fair, or Thrill Data
- Scrape Thrill Data aggressively; prefer links + embedded weekly snapshot + local history
- Store Reddit credentials; public endpoints only
