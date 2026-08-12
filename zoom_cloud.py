"""Zoom cloud recording + auto-transcript retrieval — the only way to get a transcript for a
meeting joined from the Zoom mobile app, since no audio touches this PC in that case. Requires
a Zoom Server-to-Server OAuth app (self-serve via the Zoom Marketplace) with ZOOM_ACCOUNT_ID /
ZOOM_CLIENT_ID / ZOOM_CLIENT_SECRET in .env, plus cloud recording + audio transcript +
automatic recording enabled on the Zoom account.

PARKED as of 2026-08: BCG blocks Zoom Marketplace app creation, so this path has no way to
authenticate right now and is currently dormant — meeting_watcher.py checks is_configured()
before ever calling into this module, so a Zoom meeting just falls back to local capture
without wasting time waiting on this. Nothing here needs to change if/when Marketplace access
is unblocked — just set the three env vars and it starts being used automatically.
"""

from __future__ import annotations

import os
from base64 import b64encode
from datetime import datetime, timedelta

import requests

TOKEN_URL = "https://zoom.us/oauth/token"
API_BASE = "https://api.zoom.us/v2"


def is_configured() -> bool:
    return bool(
        os.environ.get("ZOOM_ACCOUNT_ID")
        and os.environ.get("ZOOM_CLIENT_ID")
        and os.environ.get("ZOOM_CLIENT_SECRET")
    )


def _get_access_token() -> str:
    account_id = os.environ.get("ZOOM_ACCOUNT_ID")
    client_id = os.environ.get("ZOOM_CLIENT_ID")
    client_secret = os.environ.get("ZOOM_CLIENT_SECRET")
    if not all([account_id, client_id, client_secret]):
        raise RuntimeError("ZOOM_ACCOUNT_ID / ZOOM_CLIENT_ID / ZOOM_CLIENT_SECRET not set in .env")

    auth = b64encode(f"{client_id}:{client_secret}".encode()).decode()
    resp = requests.post(
        TOKEN_URL,
        params={"grant_type": "account_credentials", "account_id": account_id},
        headers={"Authorization": f"Basic {auth}"},
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


def find_transcript(title: str, start_time: datetime, tolerance_minutes: int = 30) -> dict | None:
    """Polls this user's cloud recordings for one matching `title` within
    tolerance_minutes of `start_time`. Returns {"vtt_url", "meeting_uuid", "access_token"} if
    a transcript file exists, else None — meaning the recording isn't ready yet, or cloud
    recording/transcript wasn't enabled for that meeting, in which case the caller should fall
    back to whatever local capture produced, if anything."""
    token = _get_access_token()
    day = start_time.date()
    resp = requests.get(
        f"{API_BASE}/users/me/recordings",
        params={"from": (day - timedelta(days=1)).isoformat(), "to": (day + timedelta(days=1)).isoformat()},
        headers={"Authorization": f"Bearer {token}"},
        timeout=15,
    )
    resp.raise_for_status()
    meetings = resp.json().get("meetings", [])

    window = timedelta(minutes=tolerance_minutes)
    for m in meetings:
        if m.get("topic", "").strip().lower() != title.strip().lower():
            continue
        try:
            m_start = datetime.fromisoformat(m["start_time"].replace("Z", "+00:00")).replace(tzinfo=None)
        except (KeyError, ValueError):
            continue
        if abs(m_start - start_time) > window:
            continue
        for f in m.get("recording_files", []):
            if f.get("file_type") == "TRANSCRIPT":
                return {"vtt_url": f["download_url"], "meeting_uuid": m.get("uuid"), "access_token": token}
    return None


def download_transcript(vtt_url: str, access_token: str) -> str:
    """Downloads the .vtt and strips it to plain text (drops cue numbers/timestamp lines),
    matching the plain-text shape transcribe.py produces so meeting_summarize.py can treat
    both sources identically."""
    resp = requests.get(vtt_url, headers={"Authorization": f"Bearer {access_token}"}, timeout=30)
    resp.raise_for_status()
    return _vtt_to_text(resp.text)


def _vtt_to_text(vtt: str) -> str:
    lines = []
    for line in vtt.splitlines():
        line = line.strip()
        if not line or line == "WEBVTT" or line.isdigit() or "-->" in line:
            continue
        lines.append(line)
    return "\n".join(lines)
