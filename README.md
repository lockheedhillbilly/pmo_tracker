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
| `dashboard.py` | Flask routes for the local web dashboard |
| `templates/index.html`, `static/dashboard.{css,js}` | The dashboard's UI — filters, inline editing, saved views, Gantt-style views |
| `nlu.py` | Turns a messy note into structured task fields via Claude |
| `process_email_updates.py` | Reads "PMO:"-tagged emails and applies them via `nlu.py` |
| `send_digest.py` | Emails a formatted summary of open tasks |
| `run_cycle.py` | Runs the two above in sequence — the single entry point for scheduling |
| `test_db.py` | Unit tests for `db.py` — run with `pytest` |
| `pyproject.toml` | Tells Vercel where the Flask app is (`dashboard:app`) and what build step to run |
| `build_vercel.py` | Vercel build step — mirrors `static/` into `public/static/`, since Vercel serves static assets from `public/**`, not a Flask app's own static folder |
| `test_email_parsing.py` | Unit tests for the email-parsing helpers in `process_email_updates.py` |
| `.github/workflows/pmo-cycle.yml` | Runs `run_cycle.py` twice daily via GitHub Actions — no local machine needed |

Tasks have an `owner` (the one accountable person) and an optional
`collaborators` field (comma-separated) for others helping on the same
item — deliberately not a combined string like `"A & B"`, so filtering by
person still works correctly for everyone involved.

## Current status vs. where this is headed

`db.py` now talks to the database through
[libsql](https://github.com/tursodatabase/libsql-client-py), which speaks
the same SQL either way:
- **No `TURSO_DATABASE_URL` set** (default): reads/writes a local `tasks.db`
  file, same as before — nothing changes for local dev.
- **`TURSO_DATABASE_URL` set**: reads/writes a [Turso](https://turso.tech)
  hosted database instead. Turso's CLI has no native Windows build (its
  install script explicitly rejects Git Bash/MSYS, and its GitHub releases
  only ship macOS/Linux binaries) — set it up from the **web dashboard**
  instead: sign up free, create a database, and copy the Database URL and an
  Auth Token into `.env` (see `.env.example`). No CLI needed.

Email now goes through Gmail via IMAP (read) and SMTP (send) with an App
Password — not Outlook COM (which only works on a Windows machine with
Outlook installed and logged in) and not the Gmail REST API either (its
restricted-scope mail access requires Google's paid CASA security
assessment to get a non-expiring token — disproportionate for a personal
tool). An App Password sidesteps both problems: no OAuth, no Cloud Console,
just `imaplib`/`smtplib` (both Python stdlib).

Planned next steps to make this actually hosted:
1. ~~Swap the SQLite file for a hosted database.~~ Done — see above.
2. ~~Deploy the dashboard to Vercel.~~ Done — `pyproject.toml` points Vercel's
   Python runtime at `dashboard:app`, and `build_vercel.py` mirrors `static/`
   into `public/static/` at build time (Vercel serves static assets from
   `public/**`, not a Flask app's own static folder). To deploy: import this
   repo at [vercel.com](https://vercel.com) (sign in with GitHub), and set
   `TURSO_DATABASE_URL`, `TURSO_AUTH_TOKEN`, and `ANTHROPIC_API_KEY` as
   Environment Variables in the Vercel project settings — never commit them.
3. ~~Replace Outlook COM automation.~~ Done — see above. Requires
   **2-Step Verification** enabled on the Gmail account, then an App
   Password generated at myaccount.google.com -> Security -> App Passwords.
4. ~~Move the twice-daily schedule off Windows Task Scheduler.~~ Done —
   `.github/workflows/pmo-cycle.yml` runs `run_cycle.py` at 7:30 AM/PM IST
   via GitHub Actions. Add these as repository secrets (Settings -> Secrets
   and variables -> Actions): `ANTHROPIC_API_KEY`, `PMO_TRACKER_DIGEST_TO`,
   `GMAIL_ADDRESS`, `GMAIL_APP_PASSWORD`, `TURSO_DATABASE_URL`,
   `TURSO_AUTH_TOKEN`. You can also trigger a run manually from the
   Actions tab (workflow_dispatch) to test before waiting for the schedule.

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
