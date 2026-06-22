# Theme Park Performance Monitor

Live dashboard tracking **Six Flags** and **Cedar Fair** park performance, auto-refreshed hourly via GitHub Actions and served on GitHub Pages.

🔗 **[View live dashboard](https://markhuang0625.github.io/park-monitor/)**

## Data sources

- **Live wait times** — [Queue-Times.com](https://queue-times.com/) API (free, attribution required)
- **Weekly wait trends** — [Thrill Data](https://www.thrill-data.com/waits/) averages + WoW % change
- **Reddit sentiment** — customer posts classified by park and KPI (waits, closures, crowding, staffing, etc.) via PullPush.io / Reddit public JSON
- **Year-over-year** — built from daily snapshots in `data/wait_history.jsonl`; needs ~365 days for full y/y

## How it works

Every hour, GitHub Actions runs:

1. `park_monitor.py --refresh --demo` — fetches live wait times, Thrill Data, and Reddit posts; writes `dashboard_data.json` and `dashboard.html`
2. `make_share.py` — bundles everything into a self-contained `dashboard_share.html`
3. `cp dashboard_share.html index.html` — updates the GitHub Pages entry point
4. `git-auto-commit-action` — commits and pushes `index.html` and `data/`

The workflow also runs on `workflow_dispatch` (manual trigger) from the Actions tab.

## Park open / closed logic

A park is marked **open** when live ride data is available **and** the data is less than 3 hours old. Parks outside operating hours show a **Closed** badge instead of wait times.

## Repository structure

| Path | Purpose |
| --- | --- |
| `park_monitor.py` | Core script: fetches data, classifies Reddit posts, writes outputs |
| `make_share.py` | Builds the self-contained standalone HTML |
| `index.html` | GitHub Pages entry point (auto-generated each run, do not edit) |
| `config.json` | Park IDs, subreddits, and KPI keyword patterns |
| `requirements.txt` | Python dependencies (`requests`) |
| `.github/workflows/refresh.yml` | Hourly auto-refresh workflow |
| `data/reddit_posts.jsonl` | Cached Reddit posts (persisted between runs) |
| `data/wait_history.jsonl` | Daily wait snapshots for trend/y/y charts |
| `data/thrill_data_snapshot.json` | Thrill Data weekly metrics cache |

## Local development

```bash
pip install -r requirements.txt
python park_monitor.py --refresh        # fetch live data
# add --demo if Reddit/PullPush is blocked on your network
python make_share.py                    # build standalone HTML
open dashboard_share.html
```

Edit `config.json` to add parks, subreddits, or KPI keyword patterns.

## Attributions

- Wait times: **Powered by [Queue-Times.com](https://queue-times.com/)**
- Historical weekly averages: **[Thrill Data](https://www.thrill-data.com/waits/)**
- Reddit data: PullPush.io archive + Reddit public JSON

Not affiliated with Six Flags, Cedar Fair, Reddit, Queue-Times, or Thrill Data.
