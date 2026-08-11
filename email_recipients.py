"""Persistent list of frequent email recipients for the Board's Email button. Stored
in the same `settings` table as the project-context doc, not a local JSON file — a
file next to the DB isn't viable here since Vercel's filesystem is read-only at
runtime (see db.py's settings table for the same reasoning).
"""

from __future__ import annotations

import json

from db import TaskStore

SETTING_KEY = "email_recipients"


def list_recipients(store: TaskStore) -> list[dict]:
    raw = store.get_setting(SETTING_KEY)
    return json.loads(raw) if raw else []


def add_recipient(store: TaskStore, name: str, email: str) -> list[dict]:
    if not email or "@" not in email:
        raise ValueError("A valid email address is required")
    recipients = list_recipients(store)
    if not any(r["email"].lower() == email.lower() for r in recipients):
        recipients.append({"name": name.strip() or email, "email": email.strip()})
        store.set_setting(SETTING_KEY, json.dumps(recipients))
    return recipients


def remove_recipient(store: TaskStore, email: str) -> list[dict]:
    recipients = [r for r in list_recipients(store) if r["email"].lower() != email.lower()]
    store.set_setting(SETTING_KEY, json.dumps(recipients))
    return recipients
