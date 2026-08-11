"""Sends the latest export via Gmail (SMTP with an App Password — same account and
mechanism as send_digest.py). Unlike the Outlook-COM version this was ported from,
there's no "open a draft for review" step possible from a hosted server — a website
can't reach into and control an application on the visitor's machine. This sends
directly, which is what the Board's Email button actually asks for (recipient
checkboxes + one click), not a review-then-send flow.
"""

from __future__ import annotations

from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from send_digest import GMAIL_ADDRESS, GMAIL_APP_PASSWORD

import smtplib


def compose_with_attachment(
    to_emails: list[str], subject: str, body: str, attachment_bytes: bytes, attachment_name: str,
) -> None:
    if not GMAIL_ADDRESS or not GMAIL_APP_PASSWORD:
        raise RuntimeError("GMAIL_ADDRESS and GMAIL_APP_PASSWORD must be set in .env")

    msg = MIMEMultipart()
    msg["Subject"] = subject
    msg["From"] = GMAIL_ADDRESS
    msg["To"] = ", ".join(to_emails)
    msg.attach(MIMEText(body, "plain"))

    part = MIMEApplication(attachment_bytes, Name=attachment_name)
    part["Content-Disposition"] = f'attachment; filename="{attachment_name}"'
    msg.attach(part)

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
        server.sendmail(GMAIL_ADDRESS, to_emails, msg.as_string())
