"""The long-running background listener: polls the Outlook calendar every ~60s, and for any
online (Teams/Zoom) meeting currently in progress, records+transcribes+summarizes+uploads it,
writing results into the same database the dashboard and email/paste flows use. Meant to be
started once (Task Scheduler/launchd, "at login", restart-on-failure) and left running — unlike
run_cycle.py's twice-daily batch, this needs to notice a meeting starting within about a
minute, not twice a day.

NOT YET RECONCILED with this repo's db.py: this was carried over from a local, pre-Turso
prototype that called a separate `MeetingStore` class with a different method signature
(`add_meeting(..., end_time=...)`, `update_meeting(..., audio_path=..., transcript_path=...)`)
than the `TaskStore.add_meeting`/`update_meeting` methods that actually exist here (no
`end_time` param on add_meeting, no `audio_path`/`transcript_path` params on update_meeting —
see the `meetings` table in db.py's SCHEMA_STATEMENTS and TaskStore's add_meeting/update_meeting/
list_meetings/get_meeting). Running this as-is against this repo's db.py will raise
TypeError on those unexpected keyword arguments — needs the call sites below adjusted to the
TaskStore API before this actually writes anywhere useful. Kept here verbatim (not rewritten)
so nothing is lost/guessed at in a rush; fixing the call sites is separate follow-up work.

v1 limitations (see README.md): only one recording at a time — an overlapping second meeting
is skipped until the first ends; Teams meetings joined from a phone aren't covered (no local
audio, and Teams' cloud-transcript API needs a tenant-level Azure AD app registration this
doesn't attempt) — only Zoom's cloud recording/transcript is used as the phone-join fallback.
"""

from __future__ import annotations

import os
import re
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

if sys.platform == "win32":
    # Needed behind a corporate TLS-intercepting proxy on Windows dev machines — see db.py's
    # identical guard for why this must not run unconditionally on other platforms.
    try:
        import truststore
        truststore.inject_into_ssl()
    except Exception:
        pass

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent))
load_dotenv(Path(__file__).resolve().parent / ".env")

import calendar_watch  # noqa: E402
import drive_upload  # noqa: E402
import meeting_summarize  # noqa: E402
import nlu  # noqa: E402
import transcribe  # noqa: E402
import zoom_cloud  # noqa: E402
from db import MeetingStore, TaskStore  # noqa: E402
from audio_capture import AudioCapture  # noqa: E402

DEFAULT_DB_PATH = str(Path(__file__).parent / "tasks.db")
DB_PATH = os.environ.get("PMO_TRACKER_DB_PATH", DEFAULT_DB_PATH)

# Deliberately not synced anywhere — recordings are large and there's no reason to sync raw
# audio off this machine. Point this at wherever makes sense on the machine it's running on.
DEFAULT_MEETINGS_DIR = str(Path.home() / "PMO_Tracker_Meetings")
MEETINGS_DIR = Path(os.environ.get("PMO_TRACKER_MEETINGS_DIR", DEFAULT_MEETINGS_DIR))

POLL_SECONDS = 60
ZOOM_CLOUD_WAIT_SECONDS = 300  # give Zoom time to process the cloud recording before checking once
TEAM_TZ = ZoneInfo("Asia/Kolkata")
LOG_PATH = Path(__file__).parent / "meeting_watcher.log"


def log(msg: str) -> None:
    line = f"{datetime.now(TEAM_TZ).isoformat()}  {msg}"
    if sys.stdout is not None:  # sys.stdout is None under pythonw.exe (no console) — don't crash on it
        print(line)
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def _slug(title: str) -> str:
    return re.sub(r"[^a-zA-Z0-9]+", "-", title).strip("-").lower()[:50] or "meeting"


def meeting_folder(title: str, start_time: str) -> Path:
    folder = MEETINGS_DIR / f"{start_time[:10]}_{_slug(title)}"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _format_summary_md(meeting: dict, summary: dict) -> str:
    lines = [f"# {meeting['title']}", f"_{meeting['start_time']}_", "", "## Summary", summary.get("tl_dr", ""), ""]
    if summary.get("decisions"):
        lines += ["## Decisions", *[f"- {d}" for d in summary["decisions"]], ""]
    if summary.get("next_steps"):
        lines += ["## Next steps", *[
            f"- **{s.get('owner')}**: {s.get('task')}" + (f" (due {s['due']})" if s.get("due") else "")
            for s in summary["next_steps"]
        ], ""]
    if summary.get("notes"):
        lines += ["## Notes", summary["notes"]]
    return "\n".join(lines)


def process_meeting(
    store: TaskStore, meeting_store: MeetingStore, meeting: dict, wav_path: Path | None,
    provider: str, folder: Path,
) -> None:
    """Runs off the poll loop's thread so a slow transcribe/summarize/upload pass never
    delays noticing the next meeting starting."""
    transcript, source = None, None

    if wav_path and wav_path.exists():
        try:
            transcript = transcribe.transcribe(wav_path)
            source = "local_whisper"
        except Exception as e:
            log(f"  local transcription failed: {e}")

    if provider == "zoom" and zoom_cloud.is_configured():
        time.sleep(ZOOM_CLOUD_WAIT_SECONDS)  # cloud recording processing isn't instant
        try:
            cloud = zoom_cloud.find_transcript(meeting["title"], datetime.fromisoformat(meeting["start_time"]))
            if cloud:
                transcript = zoom_cloud.download_transcript(cloud["vtt_url"], cloud["access_token"])
                source = "zoom_cloud"  # prefer cloud — covers phone-joined audio local capture can't
        except Exception as e:
            log(f"  zoom cloud transcript fetch failed: {e}")
    elif provider == "zoom":
        log("  Zoom cloud not configured (parked — see README) — using local capture only")

    if not transcript:
        meeting_store.update_meeting(meeting["id"], status="failed")
        log(f"  No transcript available for #{meeting['id']} ({meeting['title']!r}) — marked failed")
        return

    transcript_path = folder / "transcript.txt"
    transcript_path.write_text(transcript, encoding="utf-8")

    summary = None
    try:
        client = nlu.get_client()
        summary = meeting_summarize.summarize_transcript(client, transcript, meeting["title"])
        created = meeting_summarize.push_next_steps(store, summary, meeting["title"], meeting["start_time"][:10])
        log(f"  Summarized; pushed {len(created)} next-step task(s)")
    except Exception as e:
        log(f"  summarization failed: {e}")

    drive_link = None
    if summary:
        try:
            summary_path = folder / "summary.md"
            summary_path.write_text(_format_summary_md(meeting, summary), encoding="utf-8")
            drive_link = drive_upload.upload_summary(summary_path, f"{meeting['title']} - {meeting['start_time'][:10]}.md")
        except Exception as e:
            log(f"  drive upload failed: {e}")

    meeting_store.update_meeting(
        meeting["id"], transcript_source=source, transcript_path=str(transcript_path),
        summary=summary, drive_link=drive_link, status="done",
    )
    log(f"  Done: #{meeting['id']} ({meeting['title']!r}), source={source}, drive_link={'yes' if drive_link else 'no'}")


def main() -> None:
    import win32com.client

    outlook = win32com.client.Dispatch("Outlook.Application")
    ns = outlook.GetNamespace("MAPI")
    store = TaskStore(DB_PATH)
    meeting_store = MeetingStore(DB_PATH)

    current = None  # {"row": dict, "capture": AudioCapture, "om": OnlineMeeting}
    log("=== meeting_watcher started ===")

    while True:
        try:
            active = calendar_watch.get_active_meetings(ns)
        except Exception as e:
            log(f"calendar poll failed: {e}")
            active = []

        if current is None and active:
            om = active[0]  # v1: one recording at a time; a second concurrent meeting is skipped
            row = meeting_store.add_meeting(
                title=om.title, start_time=om.start.isoformat(), end_time=om.end.isoformat(),
                organizer=om.organizer, attendees=om.attendees, join_url=om.join_url, status="recording",
            )
            capture = AudioCapture()
            try:
                capture.start()
                current = {"row": row, "capture": capture, "om": om}
                log(f"Recording started: {om.title!r} ({om.provider})")
            except Exception as e:
                log(f"Failed to start capture for {om.title!r}: {e}")
                meeting_store.update_meeting(row["id"], status="failed")

        elif current is not None and current["om"] not in active:
            row, capture, om = current["row"], current["capture"], current["om"]
            current = None
            folder = meeting_folder(om.title, row["start_time"])
            wav_path = folder / "audio.wav"
            try:
                capture.stop(wav_path)
            except Exception as e:
                log(f"Failed to stop/save capture for {om.title!r}: {e}")
                wav_path = None
            meeting_store.update_meeting(row["id"], status="processing", audio_path=str(wav_path) if wav_path else None)
            log(f"Recording ended: {om.title!r} — processing in background")
            threading.Thread(
                target=process_meeting,
                args=(store, meeting_store, row, wav_path, om.provider, folder),
                daemon=True,
            ).start()

        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
