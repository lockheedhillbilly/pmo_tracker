# Orientation for a fresh Claude Code session

If you're reading this because you just opened this repo for the first time (new machine,
new session, whatever) — this file is written for you. Read `PROJECT_CONTEXT.md` next for
the team/workstreams/conventions the parser itself relies on; `SETUP_GUIDE.md` if you're
setting this up for a genuinely different engagement; `MIGRATION.md` if this is specifically
a Windows -> Mac move in progress.

## What this is

A personal, chat-driven PMO task tracker for Akshit (BCG Project Leader). Talk to it in plain
English — via Claude Code, email, or the dashboard's Capture tab — and it keeps a shared task
list, Gantt schedule, and meeting log up to date. Originally prototyped inside a BCG case repo,
then deliberately scoped out into this standalone, non-client repo (sample/dummy task data
only, no client-identifying content).

## Where things actually live

- **Code + history**: this repo, `github.com/lockheedhillbilly/pmo_tracker`, branch `master`.
- **Real data**: Turso (hosted libsql), not any local file. Every deployment (local dev,
  Vercel prod) that has `TURSO_DATABASE_URL`/`TURSO_AUTH_TOKEN` set talks to the *same*
  database — that's the actual source of truth, not any one machine.
- **Live app**: `pmo-tracker-weld.vercel.app` (primary; auto-deploys on every push to
  `master`) and `pmo-tracker-standalone.vercel.app` (a domain alias pointed at the same
  deployment — its own GitHub auto-deploy is deliberately disconnected because it was never
  given the right env vars; re-run `vercel alias set pmo-tracker-weld.vercel.app
  pmo-tracker-standalone.vercel.app` after a deploy if you want the mirror to catch up).
- **Automation**: `.github/workflows/pmo-cycle.yml` runs the twice-daily email digest via
  GitHub Actions — cloud-side, not dependent on any laptop being on.

## Current state (as of 2026-08-12)

Shipped and live: Board (pivoting, inline edit, audit trail, dependencies, custom fields,
drag-to-nest subtasks with collapse/expand), Gantt tab (CPM scheduling engine, SVG timeline,
Excel import/export), This Week tab (category buckets / by-day / last-this-next-week views),
Capture tab (paste-to-parse via Claude, a persistent timestamped history of every paste, and a
separate free-text Scratchpad sub-tab), Meetings tab, Project tab (this engagement's
context doc, editable from the dashboard), an Email button (Gmail SMTP, not Outlook — a
hosted server can't drive a visitor's local Outlook), and a real-time-ish local
meeting-watcher pipeline (see below).

71 tests pass (`pytest -q`). Run `python dashboard.py` for a local server at
`localhost:5057` against the same Turso data as prod.

## Known gaps — don't assume these are done

- **`meeting_watcher.py`** calls a `MeetingStore` class with a different method signature
  (`add_meeting(..., end_time=...)`, `update_meeting(..., audio_path=..., transcript_path=
  ...)`) than what `db.py` actually has (`TaskStore.add_meeting`/`update_meeting`, no those
  params). It's a carried-over local prototype, never reconciled with this repo's Turso-based
  `meetings` table. Fix the call sites before expecting it to write anywhere real.
- **`audio_capture.py`** (WASAPI loopback via `pyaudiowpatch`) and **`calendar_watch.py`**/
  **`meeting_watcher.py`**'s Outlook COM calls (`win32com.client`) are Windows-only by
  construction — no macOS equivalent exists yet. Needs a different capture/calendar backend
  before this pipeline can run on a non-Windows machine.
- **Capture history** has no delete endpoint yet — entries accumulate with no UI to remove one.
- The original BCG case repo (`sales_ai_copilot`, a *different* repo — see below) has
  uncommitted local changes its git remote couldn't be reached to push from a prior session
  (`Repository not found` on fetch — likely a credentials/access issue specific to that
  sandboxed session, not a real repo problem). And separately, that repo's `.env.example`
  (a template file, meant to hold blanks) has real-looking committed secrets — flagged to the
  user, not fixed here; don't carry those values into anything in *this* repo.

## Conventions worth knowing before touching code

- Real production data lives on the shared Vercel deployment — treat it like a live system,
  not a scratch environment. Clean up any test/scratch tasks created while verifying a change.
- Every feature change gets: tested locally against real Turso data (browser, not just
  `pytest`), committed, pushed, and verified live on `pmo-tracker-weld.vercel.app` before
  calling it done — a push here auto-deploys.
- `.env` (and `.env.local`) are gitignored and never committed — see `MIGRATION.md`'s
  checklist for the full list of variables and where each one's value comes from.
