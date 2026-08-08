"""Shared LLM parsing: raw text -> structured tracker actions. Used by both
process_email_updates.py and the dashboard's paste-to-create feature, so the
parsing rules only live in one place.
"""

import json
import os
from pathlib import Path

from dotenv import load_dotenv

# Importing db runs its (Windows-only) truststore setup — see db.py. Doing it
# there once, rather than duplicating the same logic here, since this module
# always imports db anyway.
from db import PRIORITIES, TRACKS, TaskStore

PROJECT_ROOT = Path(__file__).resolve().parent
load_dotenv(PROJECT_ROOT / ".env")

MODEL = "claude-sonnet-5"

SYSTEM_PROMPT = f"""You turn a raw PMO status note into structured tracker actions.

Tracks: {", ".join(TRACKS)}. Priorities: {", ".join(PRIORITIES)}.
Modules are free text use-cases, typically one of: Account Prioritization,
Account Intelligence, Seller Copilot, Overall UI — but infer a sensible new
one if the note clearly doesn't fit those.

You will be given the current list of open tasks and the raw note text.
For each distinct action item in the note:
- If it clearly refers to an existing open task (same owner, same subject
  matter), emit an "update" action with that task's id and ONLY the fields
  that changed (e.g. status, due, owner, priority, task).
- Otherwise emit an "add" action with track, owner, task, module, priority
  (default "Normal"), and due ONLY if a date is explicitly stated in the
  note — omit due entirely if not stated, do not guess a date.
- If a note statement reads as already completed (past tense, "done",
  "finished"), set status to "Done" (as part of an update, or by adding
  then flagging status "Done" via the add action's "status" field).
- Never invent a new owner from a name that's merely mentioned in the note
  (a contact, a recipient, someone being reached out to). Only use a name
  as "owner" if the note clearly assigns responsibility to that person for
  doing the work. If the note is written under/about a specific person's
  updates (e.g. it's a status report from or about them) and a task inside
  it doesn't name a different responsible person, that surrounding person
  is the owner — do not leave it blank or guess a bystander's name instead.
- Two people mentioned together are two separate owners with two separate
  add actions UNLESS the note clearly describes one single effort the two
  of them are doing jointly (e.g. "Sparsh and Abhishek to pair on X") — in
  that case only, emit ONE action with the first-named person as "owner"
  (the accountable one) and the other person(s) as a comma-separated
  "collaborators" string, e.g. owner="Sparsh", collaborators="Abhishek".
  Never combine names into a single owner string like "Sparsh & Abhishek" —
  that breaks per-person filtering. Default to separate owners when in
  doubt; joint ownership is the exception, not the default.
- If default field values are supplied (e.g. a current owner/use-case
  context from where the note was entered), use them for any action item
  that doesn't clearly state its own owner/use-case — don't leave a task
  under those defaults orphaned just because a line didn't repeat them.

Respond with ONLY valid JSON, no prose, no markdown fences:
{{"actions": [{{"type": "add"|"update", ...fields..., "id": <int, only for update>}}],
  "notes": "<anything ambiguous worth flagging to a human, or empty string>"}}
"""


def get_client():
    import anthropic

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY not set in .env")
    return anthropic.Anthropic(api_key=api_key)


def call_claude(client, note_text: str, open_tasks: list[dict], defaults: dict | None = None) -> dict:
    context = f"Default field values for this note, if not otherwise stated: {json.dumps(defaults)}\n\n" if defaults else ""
    user_content = (
        f"Current open tasks:\n{json.dumps(open_tasks, indent=2)}\n\n"
        f"{context}Raw note:\n{note_text}"
    )
    resp = client.messages.create(
        model=MODEL,
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


def apply_actions(store: TaskStore, actions: list[dict]) -> tuple[list[str], list[dict]]:
    """Returns (human-readable change descriptions, resulting task dicts)."""
    changes, results = [], []
    for action in actions:
        action = dict(action)
        kind = action.pop("type", None)
        try:
            if kind == "add":
                result = store.add_task(**action)
                changes.append(f"Added #{result['id']} for {result['owner']}: {result['task']}")
                results.append(result)
            elif kind == "update":
                task_id = action.pop("id")
                result = store.update_task(id=task_id, **action)
                changed_fields = ", ".join(f"{k}={v}" for k, v in action.items())
                changes.append(f"Updated #{task_id} ({result['owner']}): {changed_fields}")
                results.append(result)
            else:
                changes.append(f"SKIPPED unrecognized action type: {kind!r}")
        except Exception as e:
            changes.append(f"FAILED action {action} — {e}")
    return changes, results
