# Theme Park Performance Monitor

Monitor **Six Flags** and **Cedar Fair** park performance using publicly available data:

- **Reddit** — customer posts classified by park and soft KPI (waits, closures, crowding, staffing, etc.)
- **Live wait times** — [Queue-Times.com](https://queue-times.com/) API (free, attribution required)
- **Weekly wait trends** — [Thrill Data](https://www.thrill-data.com/waits/) averages + WoW % change
- **Year-over-year** — built from daily local snapshots (`data/wait_history.jsonl`); needs ~365 days for full y/y

## Quick start

```powershell
cd park_monitor
pip install -r requirements.txt
python park_monitor.py --refresh
# or
.\open_dashboard.ps1 -Refresh
```

Open `dashboard.html` in a browser. Structured data for Claude/AI tools is in `dashboard_data.json`.

## Claude / Cursor usage

This project includes a **Cursor skill** at `.cursor/skills/park-monitor/SKILL.md`. After opening this folder in Cursor, ask:

- *"Refresh the park monitor dashboard"*
- *"Which Six Flags parks had the most ride closure mentions on Reddit this week?"*
- *"Compare Cedar Fair vs Six Flags average wait times"*
- *"Show y/y wait trends for Cedar Point"*

Claude should:

1. Run `python park_monitor.py --refresh` (add `--demo` if Reddit is blocked on your network)
2. Read `dashboard_data.json` and/or `dashboard_summary.md`
3. Cite underlying sources (Reddit permalinks, queue-times URLs, thrill-data.com links)

## Data files

| File | Purpose |
| --- | --- |
| `dashboard.html` | Interactive dashboard |
| `dashboard_data.json` | Full structured export for AI tools |
| `dashboard_summary.md` | Markdown summary |
| `data/reddit_posts.jsonl` | Cached Reddit posts |
| `data/wait_history.jsonl` | Daily wait snapshots for trends/y/y |
| `data/thrill_data_snapshot.json` | Thrill Data weekly metrics |

## Refresh schedule

For meaningful y/y charts, run daily (Task Scheduler / cron):

```powershell
python park_monitor.py --refresh
```

## Configuration

Edit `config.json` to add parks, subreddits, or KPI keyword patterns.

## Attributions

- Wait times: **Powered by [Queue-Times.com](https://queue-times.com/)**
- Historical weekly averages: **[Thrill Data](https://www.thrill-data.com/waits/)** (link out; no official public API)
- Reddit: PullPush.io archive + Reddit public JSON

Not affiliated with Six Flags, Cedar Fair, Reddit, Queue-Times, or Thrill Data.
