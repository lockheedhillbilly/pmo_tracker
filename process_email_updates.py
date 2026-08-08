"""Reads emails from Gmail with 'PMO:' anywhere in the subject (so a reply
like "RE: PMO: Update" still matches), asks Claude to turn each into tracker
add/update actions (matched against current open tasks), applies them, and
emails back a confirmation. Called by run_cycle.py on a GitHub Actions
schedule — independent of any Claude Code session or local machine.

Dedup is keyed on each email's own Message-ID header, not its subject or
thread — see processed_emails in db.py. A reply within an existing PMO:
thread is a distinct message with its own Message-ID and must still be
processed; matching on subject/thread would wrongly skip it as "already
handled" just because an earlier message in the same thread was.

Auth: needs ANTHROPIC_API_KEY, GMAIL_ADDRESS, and GMAIL_APP_PASSWORD in .env
at the project root (never hardcoded). Mail: IMAP (read) + SMTP (confirmation
send, via send_digest.send_via_gmail) with a Gmail App Password — not the
Gmail REST API, since restricted-scope mail access there requires Google's
paid CASA security assessment. Parsing rules live in nlu.py (shared with the
dashboard's paste-to-create).
"""

import email
import imaplib
import os
import re
import sys
from datetime import datetime
from email.header import decode_header
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).parent))
from db import TaskStore  # noqa: E402
import nlu  # noqa: E402

DEFAULT_DB_PATH = Path(__file__).parent / "tasks.db"
DB_PATH = os.environ.get("PMO_TRACKER_DB_PATH", str(DEFAULT_DB_PATH))
RECIPIENT = os.environ.get("PMO_TRACKER_DIGEST_TO")  # must be set in .env — no hardcoded fallback
GMAIL_ADDRESS = os.environ.get("GMAIL_ADDRESS")
GMAIL_APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD")
SUBJECT_PREFIX = "PMO:"
PROCESSED_LABEL = "PMO Processed"
TEAM_TZ = ZoneInfo("Asia/Kolkata")
LOG_PATH = Path(__file__).parent / "email_ingest.log"


def log(msg: str) -> None:
    line = f"{datetime.now(TEAM_TZ).isoformat()}  {msg}"
    print(line)
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def decode_subject(raw: str) -> str:
    parts = decode_header(raw or "")
    return "".join(
        p.decode(enc or "utf-8", errors="replace") if isinstance(p, bytes) else p
        for p, enc in parts
    )


def extract_body(msg: "email.message.Message") -> str:
    """Prefer the plain-text part; fall back to stripping tags from HTML if
    that's all a message has."""
    if not msg.is_multipart():
        charset = msg.get_content_charset() or "utf-8"
        payload = msg.get_payload(decode=True) or b""
        return payload.decode(charset, errors="replace")

    for part in msg.walk():
        if part.get_content_type() == "text/plain" and "attachment" not in str(part.get("Content-Disposition", "")):
            charset = part.get_content_charset() or "utf-8"
            return (part.get_payload(decode=True) or b"").decode(charset, errors="replace")
    for part in msg.walk():
        if part.get_content_type() == "text/html" and "attachment" not in str(part.get("Content-Disposition", "")):
            charset = part.get_content_charset() or "utf-8"
            html = (part.get_payload(decode=True) or b"").decode(charset, errors="replace")
            return re.sub("<[^<]+?>", "", html)
    return ""


def fetch_candidate_emails(imap: imaplib.IMAP4_SSL, store: TaskStore) -> list[tuple[bytes, "email.message.Message", str, str]]:
    imap.select("INBOX")
    status, data = imap.search(None, "UNSEEN")
    if status != "OK":
        return []

    matches = []
    for num in data[0].split() if data and data[0] else []:
        status, msg_data = imap.fetch(num, "(RFC822)")
        if status != "OK" or not msg_data or not msg_data[0]:
            continue
        msg = email.message_from_bytes(msg_data[0][1])
        subject = decode_subject(msg.get("Subject", ""))
        if SUBJECT_PREFIX.lower() not in subject.lower():
            continue  # substring, not startswith — catches "RE: PMO: ..." replies too
        message_id = msg.get("Message-ID") or f"<no-message-id-{num.decode()}@local>"
        if store.is_email_processed(message_id):
            continue  # already handled this exact message, regardless of thread/subject
        matches.append((num, msg, subject, message_id))
    return matches


def send_confirmation(subject: str, changes: list[str], notes: str) -> None:
    from send_digest import send_via_gmail  # reuse existing Gmail-send helper

    lines = ["Applied from your PMO: email(s):", ""]
    lines += [f"  - {c}" for c in changes] if changes else ["  (no changes)"]
    if notes:
        lines += ["", f"Note from parser: {notes}"]
    body_html = "<br>".join(l.replace(" ", "&nbsp;", 2) for l in lines)
    send_via_gmail(subject, f"<div style='font-family:Segoe UI,Arial,sans-serif;font-size:13px;'>{body_html}</div>", RECIPIENT)


def main() -> None:
    if not RECIPIENT:
        log("ERROR: set PMO_TRACKER_DIGEST_TO in .env before running this.")
        return
    if not GMAIL_ADDRESS or not GMAIL_APP_PASSWORD:
        log("ERROR: set GMAIL_ADDRESS and GMAIL_APP_PASSWORD in .env before running this.")
        return

    try:
        client = nlu.get_client()
    except RuntimeError as e:
        log(f"ERROR: {e} — skipping run.")
        return

    store = TaskStore(DB_PATH)
    imap = imaplib.IMAP4_SSL("imap.gmail.com")
    imap.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
    try:
        mails = fetch_candidate_emails(imap, store)
        if not mails:
            log("No PMO: emails to process.")
            return

        try:
            imap.create(PROCESSED_LABEL)
        except Exception:
            pass  # label already exists

        all_changes, all_notes = [], []
        for num, msg, subject, message_id in mails:
            body = extract_body(msg)
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

                store.mark_email_processed(message_id, subject)
                imap.copy(num, PROCESSED_LABEL)
                imap.store(num, "+FLAGS", "\\Seen")
            except Exception as e:
                log(f"  ERROR processing {subject!r}: {e} — leaving unread for retry.")
                all_changes.append(f"FAILED to process email {subject!r}: {e}")

        now = datetime.now(TEAM_TZ)
        subject_line = f"PMO Tracker — email update applied, {now.strftime('%a %d %b, %I:%M %p')} IST"
        send_confirmation(subject_line, all_changes, "; ".join(all_notes))
        log(f"Done. {len(all_changes)} change(s) applied across {len(mails)} email(s).")
    finally:
        imap.logout()


if __name__ == "__main__":
    main()
