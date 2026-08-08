"""Reads emails from Outlook with 'PMO:' anywhere in the subject (so a reply
like "RE: PMO: Update" still matches), asks Claude to turn each into tracker
add/update actions (matched against current open tasks), applies them, and
emails back a confirmation. Called by run_cycle.py at 7:30 AM / 7:30 PM via
Windows Task Scheduler — independent of any Claude Code session.

Dedup is keyed on each email's own permanent Outlook EntryID, not its
subject or thread — see processed_emails in db.py. A reply within an
existing PMO: thread is a distinct message with its own EntryID and must
still be processed; matching on subject/thread would wrongly skip it as
"already handled" just because an earlier message in the same thread was.

Auth: needs ANTHROPIC_API_KEY in .env at the project root (never hardcoded).
Mail: uses the local Outlook desktop app via COM — no credentials stored.
Parsing rules live in nlu.py (shared with the dashboard's paste-to-create).
"""

import os
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).parent))
from db import TaskStore  # noqa: E402
import nlu  # noqa: E402

DEFAULT_DB_PATH = Path(__file__).parent / "tasks.db"
DB_PATH = os.environ.get("PMO_TRACKER_DB_PATH", str(DEFAULT_DB_PATH))
RECIPIENT = os.environ.get("PMO_TRACKER_DIGEST_TO")  # must be set in .env — no hardcoded fallback
SUBJECT_PREFIX = "PMO:"
PROCESSED_FOLDER_NAME = "PMO Processed"
TEAM_TZ = ZoneInfo("Asia/Kolkata")
LOG_PATH = Path(__file__).parent / "email_ingest.log"


def log(msg: str) -> None:
    line = f"{datetime.now(TEAM_TZ).isoformat()}  {msg}"
    print(line)
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def get_processed_folder(inbox):
    for f in inbox.Folders:
        if f.Name == PROCESSED_FOLDER_NAME:
            return f
    return inbox.Folders.Add(PROCESSED_FOLDER_NAME)


def fetch_candidate_emails(outlook_ns, store: TaskStore):
    inbox = outlook_ns.GetDefaultFolder(6)  # olFolderInbox
    items = inbox.Items.Restrict("[Unread] = true")
    matches = []
    for item in items:
        subject = str(getattr(item, "Subject", "") or "")
        if SUBJECT_PREFIX.lower() not in subject.lower():
            continue  # substring, not startswith — catches "RE: PMO: ..." replies too
        if store.is_email_processed(item.EntryID):
            continue  # already handled this exact message, regardless of thread/subject
        matches.append(item)
    return inbox, matches


def send_confirmation(subject: str, changes: list[str], notes: str) -> None:
    from send_digest import send_via_outlook  # reuse existing Outlook-send helper

    lines = ["Applied from your PMO: email(s):", ""]
    lines += [f"  - {c}" for c in changes] if changes else ["  (no changes)"]
    if notes:
        lines += ["", f"Note from parser: {notes}"]
    body_html = "<br>".join(l.replace(" ", "&nbsp;", 2) for l in lines)
    send_via_outlook(subject, f"<div style='font-family:Segoe UI,Arial,sans-serif;font-size:13px;'>{body_html}</div>", RECIPIENT)


def main() -> None:
    import win32com.client

    if not RECIPIENT:
        log("ERROR: set PMO_TRACKER_DIGEST_TO in .env before running this.")
        return

    try:
        client = nlu.get_client()
    except RuntimeError as e:
        log(f"ERROR: {e} — skipping run.")
        return

    store = TaskStore(DB_PATH)
    outlook = win32com.client.Dispatch("Outlook.Application")
    ns = outlook.GetNamespace("MAPI")
    inbox, mails = fetch_candidate_emails(ns, store)

    if not mails:
        log("No PMO: emails to process.")
        return

    processed_folder = get_processed_folder(inbox)

    all_changes, all_notes = [], []
    for mail in mails:
        subject = mail.Subject
        body = mail.Body
        entry_id = mail.EntryID
        log(f"Processing: {subject!r}")
        try:
            open_tasks = store.list_tasks(status="Open")
            result = nlu.call_claude(client, body, open_tasks)
            changes, _ = nlu.apply_actions(store, result.get("actions", []))
            notes = result.get("notes", "")
            all_changes.extend(changes)
            if notes:
                all_notes.append(f"[{subject}] {notes}")
            for c in changes:
                log(f"  {c}")

            store.mark_email_processed(entry_id, subject)
            mail.UnRead = False
            mail.Move(processed_folder)
        except Exception as e:
            log(f"  ERROR processing {subject!r}: {e} — leaving unread for retry.")
            all_changes.append(f"FAILED to process email {subject!r}: {e}")

    now = datetime.now(TEAM_TZ)
    subject = f"PMO Tracker — email update applied, {now.strftime('%a %d %b, %I:%M %p')} IST"
    send_confirmation(subject, all_changes, "; ".join(all_notes))
    log(f"Done. {len(all_changes)} change(s) applied across {len(mails)} email(s).")


if __name__ == "__main__":
    main()
