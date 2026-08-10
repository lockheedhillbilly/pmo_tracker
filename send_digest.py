"""Formats the current tracker state and emails it via Gmail (SMTP with an App
Password — no OAuth/Cloud Console needed, since Gmail API's REST path requires
Google's paid CASA security assessment for restricted-scope mail access,
disproportionate for a personal tool). Run standalone by a GitHub Actions
schedule; does not go through the MCP server, just reads the same database
directly (via db.py, so it's Turso-aware).

HTML is table-based with inline styles for broad email client compatibility
(many clients, including Outlook's desktop rendering engine, don't support
flexbox/grid or external/embedded stylesheets reliably).
"""

import os
import smtplib
import sys
from datetime import date, datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from html import escape
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).parent))
from db import TaskStore  # noqa: E402

DEFAULT_DB_PATH = Path(__file__).parent / "tasks.db"
DB_PATH = os.environ.get("PMO_TRACKER_DB_PATH", str(DEFAULT_DB_PATH))
# Comma-separated for multiple recipients, e.g. "a@gmail.com,b@bcg.com" — must be set in .env, no hardcoded fallback
_DIGEST_TO_RAW = os.environ.get("PMO_TRACKER_DIGEST_TO")
RECIPIENTS = [r.strip() for r in _DIGEST_TO_RAW.split(",") if r.strip()] if _DIGEST_TO_RAW else []
GMAIL_ADDRESS = os.environ.get("GMAIL_ADDRESS")
GMAIL_APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD")
TEAM_TZ = ZoneInfo("Asia/Kolkata")

# Same reference point as WEEK1_MONDAY in static/dashboard.js — keep the two in sync
# so "Week N" means the same thing in the dashboard and in this email.
WEEK1_MONDAY = date(2026, 7, 13)


def week_of(d: date) -> int:
    return (d - WEEK1_MONDAY).days // 7 + 1


def _ordinal_suffix(n: int) -> str:
    if 11 <= (n % 100) <= 13:
        return "th"
    return {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")


def fmt_date_short(iso: str) -> str:
    """'2026-08-14' -> '14th Aug' — easier to scan than a full ISO date."""
    d = date.fromisoformat(iso)
    return f"{d.day}{_ordinal_suffix(d.day)} {d.strftime('%b')}"


HEADER_GREEN = "#1B4D3E"
ACCENT_GREEN = "#2E8B57"
RED = "#C0392B"
GRAY = "#6B7280"
BORDER = "#E5E7EB"

MODULE_COLORS = {
    "Account Prioritization": "#2E8B57",
    "Account Intelligence": "#2C6E9B",
    "Seller Copilot": "#7A4FBE",
    "Overall UI": "#D98B3B",
}


def module_color(module: str | None) -> str:
    return MODULE_COLORS.get(module or "", GRAY)


def badge(text: str, color: str) -> str:
    return (
        f'<span style="display:inline-block;background:{color}15;color:{color};'
        f'border:1px solid {color}55;border-radius:10px;padding:2px 8px;'
        f'font-size:11px;font-weight:600;white-space:nowrap;">{escape(text)}</span>'
    )


def task_row(t: dict, today_iso: str) -> str:
    overdue = t["status"] == "Open" and t["due"] < today_iso
    due_color = RED if overdue else GRAY
    due_short = fmt_date_short(t["due"])
    due_label = f"OVERDUE — {due_short}" if overdue else due_short
    mod = t["module"] or t["track"]
    high_flag = badge("HIGH", RED) + " " if t.get("priority") == "High" else ""
    return f"""
    <tr>
      <td style="padding:8px 4px;border-bottom:1px solid {BORDER};vertical-align:top;width:110px;">
        {badge(mod, module_color(t["module"]))}
      </td>
      <td style="padding:8px 8px;border-bottom:1px solid {BORDER};vertical-align:top;font-size:13px;color:#1F2937;">
        {high_flag}{escape(t["task"])}
      </td>
      <td style="padding:8px 4px;border-bottom:1px solid {BORDER};vertical-align:top;width:64px;
                 font-size:11px;color:{due_color};font-weight:{'700' if overdue else '400'};white-space:nowrap;">
        {due_label}
      </td>
    </tr>"""


def owner_section(owner: str, tasks: list[dict], today_iso: str) -> str:
    overdue_n = sum(1 for t in tasks if t["status"] == "Open" and t["due"] < today_iso)
    count_label = f"{len(tasks)} open" + (f", {overdue_n} overdue" if overdue_n else "")
    rows = "".join(task_row(t, today_iso) for t in tasks)
    return f"""
    <tr><td style="padding:20px 0 6px 0;">
      <span style="font-size:14px;font-weight:700;color:{HEADER_GREEN};">{escape(owner)}</span>
      <span style="font-size:12px;color:{GRAY};"> — {count_label}</span>
    </td></tr>
    <tr><td>
      <table width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse;">
        {rows}
      </table>
    </td></tr>"""


def build_digest(store: TaskStore) -> tuple[str, str]:
    open_tasks = store.list_tasks(status="Open")
    summary = store.summarize(period="daily")

    now = datetime.now(TEAM_TZ)
    today_iso = now.date().isoformat()
    heading = f"Week {week_of(now.date())} | {fmt_date_short(today_iso)} | PMO tracking"
    subject = heading

    by_owner: dict[str, list[dict]] = {}
    for t in open_tasks:
        by_owner.setdefault(t["owner"], []).append(t)

    overdue_color = RED if summary["total_overdue"] else ACCENT_GREEN
    sections = "".join(
        owner_section(owner, by_owner[owner], today_iso) for owner in sorted(by_owner)
    )

    high_priority = [t for t in open_tasks if t.get("priority") == "High"]
    high_priority_html = ""
    if high_priority:
        items = "".join(
            f'<li style="margin-bottom:4px;"><b>{escape(t["owner"])}</b>: {escape(t["task"])} '
            f'<span style="color:{GRAY};font-size:11px;">({fmt_date_short(t["due"])})</span></li>'
            for t in high_priority
        )
        high_priority_html = f"""
        <tr><td style="padding:16px 0 6px 0;">
          <span style="font-size:14px;font-weight:700;color:{RED};">&#9873; High priority</span>
        </td></tr>
        <tr><td>
          <ul style="margin:0;padding-left:18px;font-size:13px;color:#1F2937;">{items}</ul>
        </td></tr>"""

    completed_html = ""
    if summary["completed_recently"]:
        items = "".join(
            f'<li style="margin-bottom:4px;"><b>{escape(t["owner"])}</b>: {escape(t["task"])}</li>'
            for t in summary["completed_recently"]
        )
        completed_html = f"""
        <tr><td style="padding:20px 0 6px 0;">
          <span style="font-size:14px;font-weight:700;color:{ACCENT_GREEN};">&#10003; Completed in the last 24h</span>
        </td></tr>
        <tr><td>
          <ul style="margin:0;padding-left:18px;font-size:13px;color:#1F2937;">{items}</ul>
        </td></tr>"""

    empty_html = ""
    if not open_tasks:
        empty_html = f'<tr><td style="padding:16px 0;color:{GRAY};font-size:13px;">Nothing open — tracker is empty.</td></tr>'

    html = f"""
    <div style="font-family:'Segoe UI',Arial,sans-serif;max-width:700px;">
      <table width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse;">
        <tr>
          <td style="background:{HEADER_GREEN};padding:18px 20px;border-radius:6px 6px 0 0;">
            <span style="color:#fff;font-size:18px;font-weight:700;">{escape(heading)}</span><br/>
            <span style="color:#CBEFDD;font-size:12px;">{now.strftime('%A — %I:%M %p')} IST</span>
          </td>
        </tr>
        <tr>
          <td style="background:#F9FAFB;padding:14px 20px;border:1px solid {BORDER};border-top:none;">
            <span style="font-size:13px;color:#1F2937;">Open: <b>{summary['total_open']}</b></span>
            <span style="color:{BORDER};"> &nbsp;|&nbsp; </span>
            <span style="font-size:13px;color:{overdue_color};">Overdue: <b>{summary['total_overdue']}</b></span>
          </td>
        </tr>
        <tr>
          <td style="padding:0 20px 20px 20px;border:1px solid {BORDER};border-top:none;border-radius:0 0 6px 6px;">
            <table width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse;">
              {high_priority_html}
              {sections}
              {completed_html}
              {empty_html}
            </table>
          </td>
        </tr>
      </table>
      <div style="font-size:11px;color:{GRAY};padding:10px 4px;">
        PMO Tracker — automated digest, not monitored for replies.
      </div>
    </div>"""

    return subject, html


def send_via_gmail(subject: str, html_body: str, to: str | list[str]) -> None:
    if not GMAIL_ADDRESS or not GMAIL_APP_PASSWORD:
        raise RuntimeError("GMAIL_ADDRESS and GMAIL_APP_PASSWORD must be set in .env")

    recipients = [to] if isinstance(to, str) else list(to)

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = GMAIL_ADDRESS
    msg["To"] = ", ".join(recipients)
    msg.attach(MIMEText(html_body, "html"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
        server.sendmail(GMAIL_ADDRESS, recipients, msg.as_string())


def main() -> None:
    if not RECIPIENTS:
        print("ERROR: set PMO_TRACKER_DIGEST_TO in .env before running this.")
        return
    store = TaskStore(DB_PATH)
    subject, html_body = build_digest(store)
    send_via_gmail(subject, html_body, RECIPIENTS)
    print(f"Sent: {subject}")


if __name__ == "__main__":
    main()
