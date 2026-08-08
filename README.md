# PMO Tracker

A personal, chat-driven task tracker: talk to it in plain English (via Claude
Code, or via email), and it keeps a shared task list up to date. Built as a
scoped-out, standalone version of a tool originally prototyped for a BCG
case — this copy uses only sample/dummy data and has no client-specific
content.

## What's here

| File | What it does |
|---|---|
| `db.py` | SQLite schema + all the read/write logic (tasks, notes, review workflow) |
| `server.py` | MCP server — lets Claude chat clients add/update/query tasks |
| `dashboard.py` | Local web dashboard (Flask) — filters, inline editing, Gantt-style views |
| `nlu.py` | Turns a messy note into structured task fields via Claude |
| `process_email_updates.py` | Reads "PMO:"-tagged emails and applies them via `nlu.py` |
| `send_digest.py` | Emails a formatted summary of open tasks |
| `run_cycle.py` | Runs the two above in sequence — the single entry point for scheduling |

## Current status vs. where this is headed

This copy still uses the same mechanics as the original prototype:
SQLite file on local disk, and Outlook desktop automation (via `pywin32`)
for email. That only works on a Windows machine with Outlook installed and
you logged in — it will **not** work once this moves to a real host like
Vercel.

Planned next steps to make this actually hosted:
1. Swap the SQLite file for a hosted database (Turso — closest to a drop-in
   replacement since it speaks near-identical SQL).
2. Deploy the dashboard to Vercel.
3. Replace Outlook COM automation with the Microsoft Graph API, pointed at
   a dedicated mailbox — needed regardless of host, since no server can
   automate a desktop Outlook client.
4. Move the twice-daily schedule from Windows Task Scheduler to a cron
   trigger (e.g. a scheduled GitHub Actions workflow calling the hosted app).

## Running it locally (current, pre-hosting version)

```bash
pip install -r requirements.txt
cp .env.example .env   # then fill in ANTHROPIC_API_KEY and PMO_TRACKER_DIGEST_TO

python dashboard.py                 # dashboard at http://localhost:5057
python server.py                    # MCP server, stdio — for chat clients
python run_cycle.py                 # one manual ingest+digest cycle
```

The database file (`tasks.db`) is created automatically on first run, next
to the scripts. It's gitignored — it's data, not code.
