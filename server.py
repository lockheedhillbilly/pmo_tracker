"""MCP server (stdio) for the PMO task tracker.

Persistence-only: this server does no natural-language parsing. The calling
skill (Claude, reading a chat message) is responsible for extracting
structured fields from messy notes and picking the right task id before
calling update_task — these tools just read/write the shared SQLite store.
"""

import os
from pathlib import Path

from mcp.server import MCPServer

from db import TaskStore, TRACKS, PRIORITIES, STATUSES

DEFAULT_DB_PATH = Path(__file__).parent / "tasks.db"
DB_PATH = Path(os.environ.get("PMO_TRACKER_DB_PATH", str(DEFAULT_DB_PATH)))

store = TaskStore(DB_PATH)

mcp = MCPServer(
    name="pmo-tracker",
    instructions=(
        "Tracks Akshit's personal PMO action items. "
        f"Tracks are: {', '.join(TRACKS)}. Priorities: {', '.join(PRIORITIES)}. "
        f"Statuses: {', '.join(STATUSES)}. "
        "When a user's message describes work to do, extract the fields yourself "
        "and call add_task — don't ask the user to fill in a form. "
        "When a user refers to a task by description rather than id (e.g. "
        "'mark Aayushi's mapping task done'), call list_tasks first to find the "
        "matching id, then call update_task with that id. "
        "due defaults to the Friday of the current work week (IST) if omitted."
    ),
)


@mcp.tool()
def add_task(
    track: str,
    owner: str,
    task: str,
    module: str | None = None,
    due: str | None = None,
    priority: str = "Normal",
    source: str | None = None,
) -> dict:
    """Add one action item to the tracker.

    Args:
        track: One of Discovery, Tech, Data, Milestone.
        owner: Person responsible.
        task: Short description of the action item.
        module: Free-text sub-category within the track (e.g. "Lead Prioritization").
        due: ISO date (YYYY-MM-DD). Omit to default to Friday of this work week (IST).
        priority: High or Normal.
        source: Where this came from (e.g. "WhatsApp dump 2026-08-06"), optional.
    """
    return store.add_task(track=track, owner=owner, task=task, module=module,
                           due=due, priority=priority, source=source)


@mcp.tool()
def update_task(
    id: int,
    track: str | None = None,
    module: str | None = None,
    owner: str | None = None,
    task: str | None = None,
    due: str | None = None,
    priority: str | None = None,
    status: str | None = None,
) -> dict:
    """Update one or more fields of an existing task by id. Only pass the
    fields that changed. Setting due explicitly clears the "assumed" flag.
    Use status="Done" to mark complete, status="Open" to reopen."""
    return store.update_task(id=id, track=track, module=module, owner=owner,
                              task=task, due=due, priority=priority, status=status)


@mcp.tool()
def list_tasks(
    owner: str | None = None,
    track: str | None = None,
    module: str | None = None,
    status: str | None = None,
    priority: str | None = None,
    overdue_only: bool = False,
    due_this_week: bool = False,
    query: str | None = None,
) -> list[dict]:
    """List/filter tasks. All filters are optional and combine with AND.
    query does a substring match across task text, owner, and module —
    use it to find a task by description when you don't have its id."""
    return store.list_tasks(owner=owner, track=track, module=module, status=status,
                             priority=priority, overdue_only=overdue_only,
                             due_this_week=due_this_week, query=query)


@mcp.tool()
def summarize(period: str = "weekly", track: str | None = None) -> dict:
    """Roll up current tracker state: open/overdue counts, due-soon items,
    recently completed items, and a per-owner breakdown. period is "daily"
    or "weekly"; track optionally scopes to one track."""
    return store.summarize(period=period, track=track)


if __name__ == "__main__":
    mcp.run(transport="stdio")
