# Moving to a new machine (Windows -> Mac)

Written 2026-08-12, before a laptop change. Goal: pick up exactly where we left off, with
nothing lost. Read this top to bottom once before doing anything.

## The short version

Almost everything that matters is already **not** on this laptop:

- **All real task data** lives in Turso (hosted DB), not a local file. The dashboard, the
  Gantt tab, meetings, capture history, the project-context doc — all of it is already safe.
- **The live app** is deployed on Vercel (`pmo-tracker-weld.vercel.app`, mirrored at
  `pmo-tracker-standalone.vercel.app`) and keeps running regardless of what happens to this
  laptop.
- **The twice-daily email digest automation** runs on GitHub Actions, not this machine.
- **All code for the actual product** (`pmo-tracker-standalone` repo) is committed and pushed
  to GitHub — `git clone` on the new machine gets you 100% of it.

What genuinely only exists on this Windows laptop and needs deliberate handling:

1. **Secrets** (`.env` files) — gitignored by design, never pushed anywhere.
2. **The local meeting-watcher pipeline's Windows-only pieces** (Outlook COM, WASAPI audio) —
   these need a different approach on Mac, not just a re-install.
3. **Local tool logins** (`vercel` CLI, `gh` CLI if used, git credentials for push access).
4. **This Claude Code session's own memory** — tied to this machine's `~/.claude`, doesn't
   travel automatically.

## Before you touch the Windows laptop

- [ ] Confirm `git status` is clean in `C:\dev\pmo-tracker-standalone` (it is, as of this
      writing — everything through the meeting-watcher backup commit is pushed).
- [ ] **Separately**, the original `sales_ai_copilot` repo (OneDrive-synced folder) has
      uncommitted local changes and untracked files that I could **not** push from this
      session — `git fetch` to its `origin` (github.com/Gupta-Sparsh_bcgprod/sales_ai_copilot)
      returned "Repository not found," meaning this session's git credentials don't have
      access to it. Before wiping this laptop: from a terminal where you know that push
      *does* work (or after fixing access), run `git add -A && git commit && git push` in that
      folder, or at minimum confirm OneDrive has fully synced it (green checkmark, not a
      spinning/pending icon) so the raw files are backed up even if the git history isn't.
      Everything genuinely new in there (the meeting-pipeline scripts) has already been copied
      into `pmo-tracker-standalone` and pushed, so this is a secondary safety net, not the
      only copy.
- [ ] **Security**: `sales_ai_copilot/.env.example` has real-looking committed secrets
      (an Anthropic API key, a `MINDCASE_API_KEY`) — this is a template file that's supposed
      to hold blanks, not live values. Rotate both keys and consider scrubbing them from git
      history. Not touched or carried forward by anything in this guide.
- [ ] Export/copy this repo's `.env` file somewhere you'll have on the Mac (a password
      manager entry, not another git repo) — see the exact variable list below.
- [ ] Note down whether `C:\dev\pmo-tracker-standalone\tasks.db` exists and has anything in
      it. It shouldn't matter (Turso is authoritative whenever `TURSO_DATABASE_URL` is set),
      but check `TURSO_DATABASE_URL` is actually set in your `.env` right now, not just
      assumed — if it were unset, the app would silently be running off that local file
      instead, and *that* would need to actually transfer.

## The .env secrets checklist

None of these are in git. Copy the values (not this file) somewhere durable before wiping
Windows — a password manager is the right place, not a text file synced to the same
OneDrive/iCloud that could vanish along with everything else.

| Variable | Where to find/regenerate it |
|---|---|
| `ANTHROPIC_API_KEY` | console.anthropic.com -> API Keys |
| `TURSO_DATABASE_URL` | turso.tech dashboard -> your database -> connection URL |
| `TURSO_AUTH_TOKEN` | turso.tech dashboard -> your database -> create/copy a token |
| `GMAIL_ADDRESS` | the Gmail account `send_digest.py`/the Email button send from |
| `GMAIL_APP_PASSWORD` | myaccount.google.com -> Security -> App Passwords (needs 2FA on) |
| `PMO_TRACKER_DIGEST_TO` | whoever the digest email goes to (can be `GMAIL_ADDRESS` itself) |
| `GOOGLE_OAUTH_CLIENT_SECRET_PATH` | only if you set up Drive upload — path to the OAuth JSON from Google Cloud Console |
| `GOOGLE_DRIVE_FOLDER_ID` | only if you set up Drive upload — target folder's ID from its URL |
| `ZOOM_ACCOUNT_ID` / `ZOOM_CLIENT_ID` / `ZOOM_CLIENT_SECRET` | parked per `zoom_cloud.py`'s own note — BCG currently blocks Zoom Marketplace app creation, so these were never actually set. Skip unless that's changed. |

`PMO_TRACKER_DB_PATH` and `PMO_TRACKER_MEETINGS_DIR` are optional local-path overrides —
leave them unset on the Mac and let the defaults (next to the script, and `~/PMO_Tracker_Meetings`)
apply fresh there.

## Setting up the Mac

1. **Install prerequisites**
   ```bash
   xcode-select --install        # git, plus build tools some packages need
   brew install python@3.12
   ```
2. **Clone the repo**
   ```bash
   git clone https://github.com/lockheedhillbilly/pmo_tracker.git ~/dev/pmo-tracker-standalone
   cd ~/dev/pmo-tracker-standalone
   ```
3. **Recreate `.env`** — copy `.env.example` to `.env` and fill in the values from your
   password manager (the checklist above).
4. **Install Python dependencies**
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```
   Note: `truststore` and `pywin32`/`PyAudioWPatch` won't install on Mac — that's expected,
   they're gated to Windows in `requirements.txt` (`sys_platform == "win32"`) since they're
   either unnecessary (truststore) or fundamentally Windows-only (see below) on Mac.
5. **Run the test suite** to confirm the DB layer works end-to-end against Turso:
   ```bash
   pytest -q
   ```
6. **Run the dashboard locally**
   ```bash
   python dashboard.py
   ```
   Open `http://localhost:5057` — you should see the exact same live board as
   `pmo-tracker-weld.vercel.app`, since both point at the same Turso database.
7. **Vercel CLI** (only needed if you want to redeploy/manage aliases from the Mac):
   ```bash
   npm install -g vercel
   vercel login
   vercel link --project pmo-tracker   # re-links this Mac checkout to the existing project
   ```
   This is a fresh login (OAuth in a browser), not something to "transfer" — Vercel's own
   deployment is untouched regardless of whether any machine is logged into the CLI.
8. **GitHub push access** — set up a fresh SSH key or use `gh auth login` on the Mac; the
   old Windows machine's credentials don't transfer.
9. **Claude Code / MCP server**: this session's `.mcp.json` (in the *other* repo,
   `sales_ai_copilot`) points at a Windows venv path and a Windows-only local sqlite file —
   don't carry that config forward. On the Mac, register the MCP server fresh, from inside
   `pmo-tracker-standalone`:
   ```json
   {
     "mcpServers": {
       "pmo-tracker": {
         "command": ".venv/bin/python",
         "args": ["server.py"]
       }
     }
   }
   ```
   No `PMO_TRACKER_DB_PATH` override needed — with `TURSO_DATABASE_URL`/`TURSO_AUTH_TOKEN` set
   in `.env`, `server.py` talks to the same Turso database as everything else automatically.

## What a fresh Claude Code session will and won't know

- **Will know**: everything written into `PROJECT_CONTEXT.md` and `SETUP_GUIDE.md` in this
  repo (team roster, use cases, methodology, conventions) — that's exactly why those files
  exist, so a new session (or a new machine) isn't starting from zero. Open Claude Code inside
  the cloned repo and point it at these files if it doesn't pick them up on its own.
- **Won't know**: the specific back-and-forth in *this* conversation (why a given decision was
  made a certain way, things explicitly tried and rejected). If there's a decision from this
  thread you want preserved beyond what's already in the repo's own docs/comments, say so now
  and it can be written down before the move — otherwise it's lost when this session ends.

## The meeting-watcher pipeline: what needs real rework on Mac

These pieces are Windows-only by construction, not just "needs a re-install" — they were built
against Windows-specific APIs that don't exist on macOS at all:

- **`calendar_watch.py` / `meeting_watcher.py`** use `win32com.client.Dispatch("Outlook.Application")`
  — COM automation is a Windows-only mechanism. On Mac, reading calendar/meeting data needs a
  different approach entirely (Microsoft Graph API against the same Outlook/Exchange account is
  the natural replacement, or macOS Calendar.app via EventKit if the meetings show up there).
- **`audio_capture.py`** uses `pyaudiowpatch`, a WASAPI-specific fork of PyAudio — WASAPI is a
  Windows audio API. Mac equivalent would be a virtual loopback device (e.g. BlackHole) paired
  with standard `pyaudio`/`sounddevice`, which is a different enough capture model that this
  file needs a rewrite, not a port.
- **`transcribe.py`** (faster-whisper) and **`zoom_cloud.py`**/**`drive_upload.py`** (plain
  REST/Google API calls) are already portable — no changes needed there.

Separately (unrelated to the Mac move): `meeting_watcher.py` currently calls a `MeetingStore`
class with a different API shape than this repo's `db.py` actually has (see the comment at the
top of the file) — it was carried over from an earlier prototype and was never reconciled with
the Turso-based schema. That's real follow-up work whenever you're ready to actually run this
pipeline again, on whichever machine.

## Quick sanity check once you're on the Mac

```bash
curl -s https://pmo-tracker-weld.vercel.app/api/meta   # confirms prod is unaffected regardless
python dashboard.py                                     # then hit localhost:5057, compare
pytest -q                                                # 71 tests should pass
```

If all three work, you have lost nothing.
