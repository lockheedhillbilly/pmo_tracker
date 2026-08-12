"""Turns a meeting transcript into a structured summary + next steps, reusing the same
Claude-calling plumbing as nlu.py (get_client(), same ANTHROPIC_API_KEY, same model). Next
steps with a clear owner are pushed straight into the existing tasks table via
TaskStore.add_task — the same function the email and paste-to-create flows call — so meeting
action items show up on the Board tab exactly like any other task.
"""

import json

import nlu
from db import PRIORITIES, TRACKS, TaskStore

SYSTEM_PROMPT = f"""You summarize a meeting transcript for a PMO tracker.

Tracks: {", ".join(TRACKS)}. Priorities: {", ".join(PRIORITIES)}.

Respond with ONLY valid JSON, no prose, no markdown fences:
{{"tl_dr": "<2-3 sentence summary of the meeting>",
  "decisions": ["<decision made 1>", ...],
  "next_steps": [{{"owner": "<name>", "task": "<action>", "track": "<one of the tracks above>",
                   "module": "<free-text use case, or omit>", "priority": "<High or Normal, omit for Normal>",
                   "due": "<ISO date, only if explicitly stated, else omit>"}}],
  "notes": "<anything ambiguous or worth flagging to a human, or empty string>"}}

Rules:
- owner must be a specific person's name the transcript assigns that action to — never invent
  one, and never use "team"/"everyone"/"all" as an owner.
- Only include a next step if the transcript actually assigns it to someone; general
  discussion that didn't produce an action belongs in "decisions" or nowhere, not "next_steps".
- due is an ISO date (YYYY-MM-DD) only if a date/day is explicitly stated for that item in the
  transcript; omit the field entirely otherwise — never guess a date.
- If a next step doesn't clearly fit a track, use the track most of the meeting's subject
  matter falls under rather than leaving it blank.
"""


def summarize_transcript(client, transcript: str, meeting_title: str) -> dict:
    user_content = f"Meeting: {meeting_title}\n\nTranscript:\n{transcript}"
    resp = client.messages.create(
        model=nlu.MODEL,
        max_tokens=4000,
        thinking={"type": "disabled"},
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_content}],
    )
    text_block = next((b for b in resp.content if getattr(b, "type", None) == "text"), None)
    if text_block is None:
        raise ValueError(f"No text block in response (got: {[getattr(b, 'type', None) for b in resp.content]})")
    text = text_block.text.strip()
    if text.startswith("```"):
        text = text.strip("`").removeprefix("json").strip()
    return json.loads(text)


def push_next_steps(store: TaskStore, summary: dict, meeting_title: str, meeting_date: str) -> list[dict]:
    """Adds each next step with a clear owner+track as a task; returns the created task dicts.
    Steps missing owner/task/track are skipped rather than guessed — same philosophy as
    nlu.apply_actions, which fails/skips loudly rather than silently inventing a field."""
    created = []
    for step in summary.get("next_steps", []):
        owner = (step.get("owner") or "").strip()
        task_text = (step.get("task") or "").strip()
        track = step.get("track")
        if not owner or not task_text or track not in TRACKS:
            continue
        result = store.add_task(
            track=track,
            owner=owner,
            task=task_text,
            module=step.get("module"),
            due=step.get("due"),
            priority=step.get("priority", "Normal"),
            source=f"Meeting: {meeting_title} ({meeting_date})",
        )
        created.append(result)
    return created
