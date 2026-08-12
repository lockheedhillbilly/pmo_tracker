"""Detects active online (Teams/Zoom) meetings on the local Outlook calendar. Polled by
meeting_watcher.py every ~60s; matches the win32com pattern already used for the inbox in
process_email_updates.py (`win32com.client.Dispatch("Outlook.Application")`), just against
the Calendar folder instead of the Inbox.

Zoom is a 3rd-party add-in that just drops a join link into the appointment body/location —
it never sets any Outlook COM property. And Teams' own `OnlineMeetingExternalLink` can be
blank even when `IsOnlineMeeting` is True (a documented Microsoft quirk). So a join-link regex
over Body/Location is the one reliable path for both providers; `IsOnlineMeeting` is only used
as a fallback signal when no link could be parsed out of the body.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta

TEAMS_LINK_RE = re.compile(r"https?://teams\.microsoft\.com/l/meetup-join/\S+?(?=[\"'<>\s])")
ZOOM_LINK_RE = re.compile(r"https?://[a-zA-Z0-9.-]*zoom\.us/j/\S+?(?=[\"'<>\s])")


@dataclass
class OnlineMeeting:
    title: str
    start: datetime
    end: datetime
    organizer: str | None
    attendees: str | None
    join_url: str | None
    provider: str  # "teams" | "zoom"


def _extract_join_url(item) -> tuple[str | None, str]:
    text = f"{getattr(item, 'Body', '') or ''} {getattr(item, 'Location', '') or ''}"

    m = TEAMS_LINK_RE.search(text)
    if m:
        return m.group(0), "teams"
    m = ZOOM_LINK_RE.search(text)
    if m:
        return m.group(0), "zoom"
    if bool(getattr(item, "IsOnlineMeeting", False)):
        return None, "teams"  # confirmed online meeting, just couldn't parse a link out of the body
    return None, ""


def _to_datetime(com_dt) -> datetime:
    """pywin32 COM dates come back as pywintypes.datetime (tz-aware); normalize to a plain
    naive local datetime for straightforward comparison against datetime.now()."""
    return datetime(com_dt.year, com_dt.month, com_dt.day, com_dt.hour, com_dt.minute, com_dt.second)


def get_active_meetings(outlook_ns, now: datetime | None = None, buffer_minutes: int = 5) -> list[OnlineMeeting]:
    """Online (Teams/Zoom) calendar items whose [Start, End] window covers `now`, with a
    buffer_minutes grace on both ends (joining a couple minutes early / a meeting running
    over) so a 60s poll interval doesn't clip the start or stop early on a slow finish."""
    now = now or datetime.now()
    calendar = outlook_ns.GetDefaultFolder(9)  # olFolderCalendar
    items = calendar.Items
    items.Sort("[Start]")
    items.IncludeRecurrences = True

    # Coarse pre-filter only — the buffered start<=now<=end check below is what actually
    # decides membership. Restrict needs locale-formatted date literals, not ISO.
    window_start = (now - timedelta(hours=6)).strftime("%m/%d/%Y %I:%M %p")
    window_end = (now + timedelta(hours=1)).strftime("%m/%d/%Y %I:%M %p")
    restricted = items.Restrict(f"[Start] >= '{window_start}' AND [Start] <= '{window_end}'")

    buffer = timedelta(minutes=buffer_minutes)
    active: list[OnlineMeeting] = []
    for item in restricted:
        try:
            start = _to_datetime(item.Start)
            end = _to_datetime(item.End)
        except Exception:
            continue
        if not (start - buffer <= now <= end + buffer):
            continue
        join_url, provider = _extract_join_url(item)
        if not provider:
            continue  # not a recognized online meeting — respects the "only online meetings" scope
        active.append(OnlineMeeting(
            title=str(item.Subject or "Untitled meeting"),
            start=start,
            end=end,
            organizer=str(getattr(item, "Organizer", "") or "") or None,
            attendees=str(getattr(item, "RequiredAttendees", "") or "") or None,
            join_url=join_url,
            provider=provider,
        ))
    return active
