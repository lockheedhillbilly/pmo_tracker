"""SQLite-compatible persistence for the PMO tracker, via libsql (Turso when
TURSO_DATABASE_URL is set, otherwise a local file — same engine either way).
No NLU here — callers (the MCP tools in server.py, driven by Claude's own
parsing of chat messages) pass already structured fields; this module only
validates, computes defaults, and persists.
"""

from __future__ import annotations

import json
import os
import sys
from contextlib import contextmanager
from dataclasses import dataclass, asdict
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

if sys.platform == "win32":
    # Needed behind a corporate TLS-intercepting proxy on Windows dev
    # machines; certifi's bundle won't have its cert, but the OS's own cert
    # store does. Windows-only and best-effort: hosted runners (Vercel,
    # GitHub Actions) have no such proxy, and this was observed to interfere
    # with other HTTPS clients (e.g. the Anthropic SDK) on a clean Linux
    # runner despite not raising, so it must not run there at all.
    try:
        import truststore
        truststore.inject_into_ssl()
    except Exception:
        pass

import libsql_client
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / ".env")

TRACKS = ("Discovery", "Tech", "Data", "Milestone")
PRIORITIES = ("High", "Normal")
STATUSES = ("Open", "Done")
EXECUTION_STATES = ("Not started", "In progress", "Blocked")
REVIEW_STATUSES = ("Review pending", "Changes requested", "Reviewed")
REVIEW_TYPES = ("Quality check", "Decision", "Client readiness", "Other")

TEAM_TZ = ZoneInfo("Asia/Kolkata")

# One statement per entry — libsql's execute() takes a single statement at a
# time (no sqlite3-style executescript).
SCHEMA_STATEMENTS = [
    """CREATE TABLE IF NOT EXISTS tasks (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        track       TEXT NOT NULL CHECK(track IN ('Discovery','Tech','Data','Milestone')),
        module      TEXT,
        owner       TEXT NOT NULL,
        task        TEXT NOT NULL,
        added       TEXT NOT NULL,
        due         TEXT NOT NULL,
        due_assumed INTEGER NOT NULL,
        priority    TEXT NOT NULL CHECK(priority IN ('High','Normal')) DEFAULT 'Normal',
        status      TEXT NOT NULL CHECK(status IN ('Open','Done')) DEFAULT 'Open',
        updated_at  TEXT NOT NULL,
        source      TEXT
    )""",
    "CREATE INDEX IF NOT EXISTS idx_tasks_owner ON tasks(owner)",
    "CREATE INDEX IF NOT EXISTS idx_tasks_status_due ON tasks(status, due)",
    """CREATE TABLE IF NOT EXISTS notes (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        task_id    INTEGER NOT NULL,
        author     TEXT NOT NULL,
        text       TEXT NOT NULL,
        created_at TEXT NOT NULL,
        pinned     INTEGER NOT NULL DEFAULT 0,
        FOREIGN KEY(task_id) REFERENCES tasks(id)
    )""",
    "CREATE INDEX IF NOT EXISTS idx_notes_task ON notes(task_id)",
    # Dedup key is the email's own permanent id (Outlook EntryID), never the
    # subject/thread — a reply to an existing PMO: thread is a distinct message
    # with its own EntryID and must still be processed, even though it shares a
    # subject (and, after "RE:" prefixing, no longer even starts with "PMO:").
    """CREATE TABLE IF NOT EXISTS processed_emails (
        entry_id     TEXT PRIMARY KEY,
        subject      TEXT,
        processed_at TEXT NOT NULL
    )""",
    # Audit trail: one row per field that actually changed on an update_task call.
    """CREATE TABLE IF NOT EXISTS history (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        task_id    INTEGER NOT NULL,
        field      TEXT NOT NULL,
        old_value  TEXT,
        new_value  TEXT,
        changed_by TEXT NOT NULL,
        changed_at TEXT NOT NULL,
        FOREIGN KEY(task_id) REFERENCES tasks(id)
    )""",
    "CREATE INDEX IF NOT EXISTS idx_history_task ON history(task_id)",
    "CREATE INDEX IF NOT EXISTS idx_history_changed_at ON history(changed_at)",
    # Generic small key/value store — e.g. the editable project-context doc.
    # A real file on disk isn't viable here since Vercel's filesystem is
    # read-only at runtime; this is the one thing in this file NOT reachable
    # from a hosted read-only filesystem, so it needs the database instead.
    """CREATE TABLE IF NOT EXISTS settings (
        key        TEXT PRIMARY KEY,
        value      TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""",
    # Populated by a separate local process (a meeting watcher/transcriber
    # on the user's machine) connecting to this same Turso database directly
    # — not through this app's API. transcript_source is deliberately free
    # text, not a CHECK-constrained enum: only 'local_whisper' exists today,
    # but 'zoom_cloud'/'teams_graph' are expected later and shouldn't need a
    # schema migration to add.
    """CREATE TABLE IF NOT EXISTS meetings (
        id                INTEGER PRIMARY KEY AUTOINCREMENT,
        title             TEXT NOT NULL,
        start_time        TEXT NOT NULL,
        end_time          TEXT,
        organizer         TEXT,
        attendees         TEXT,
        join_url          TEXT,
        transcript_source TEXT NOT NULL DEFAULT 'local_whisper',
        transcript_text   TEXT,
        summary           TEXT,
        drive_link        TEXT,
        status            TEXT NOT NULL DEFAULT 'pending'
                              CHECK(status IN ('pending','recording','processing','done','failed')),
        tasks_created     INTEGER NOT NULL DEFAULT 0,
        created_at        TEXT NOT NULL,
        updated_at        TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS idx_meetings_start_time ON meetings(start_time)",
    "CREATE INDEX IF NOT EXISTS idx_meetings_status ON meetings(status)",
    # A running log of everything ever pasted into the Capture tab, kept even after it's
    # been parsed and applied — so "what did I dump in here on Tuesday" is answerable by
    # scrolling, not just the most recent parse result shown inline.
    """CREATE TABLE IF NOT EXISTS captures (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        text       TEXT NOT NULL,
        summary    TEXT,
        created_at TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS idx_captures_created_at ON captures(created_at)",
]

MEETING_STATUSES = ("pending", "recording", "processing", "done", "failed")

# columns added after the original schema shipped — migrated in _connect()
_MIGRATED_COLUMNS = {
    "sort_order": "INTEGER",
    "execution_state": "TEXT",
    "review_status": "TEXT",
    "reviewer": "TEXT",
    "review_type": "TEXT",
    "review_due": "TEXT",
    "review_comment": "TEXT",
    "collaborators": "TEXT",
    "blocked_by_id": "INTEGER",
    "custom_fields": "TEXT",
    # Gantt scheduling — extends this same tasks table rather than a parallel one, so
    # Board/Gantt/Table/etc. all stay views over one shared dataset.
    "parent_id": "INTEGER",          # NULL = top-level task; set = subtask of that task
    "start_date": "TEXT",            # planned start; `due` is reused as the planned end
    "baseline_start": "TEXT",        # snapshot for the Baseline-vs-Current toggle
    "baseline_end": "TEXT",
    "percent_complete": "INTEGER",
    "pinned": "INTEGER",             # 0 = auto-scheduled, 1 = manually fixed
    "dependency_type": "TEXT",       # FS/SS/FF/SF — only meaningful when blocked_by_id is set
    "lag_days": "INTEGER",           # lead (negative) / lag (positive) on that dependency
    "is_milestone": "INTEGER",       # zero-duration marker
}
DEPENDENCY_TYPES = ("FS", "SS", "FF", "SF")


class TrackerError(ValueError):
    """Raised for bad input (unknown id, invalid enum value, etc.)."""


def _now_ist() -> datetime:
    return datetime.now(TEAM_TZ)


def _today_ist() -> date:
    return _now_ist().date()


def end_of_work_week(from_date: date | None = None) -> date:
    """Friday of the current work week, IST. If today is already Sat/Sun,
    rolls forward to *next* Friday rather than a Friday already in the past."""
    d = from_date or _today_ist()
    days_until_friday = (4 - d.weekday()) % 7
    return d + timedelta(days=days_until_friday)


def _normalize_collaborators(collaborators: str | None) -> str | None:
    """Comma-separated names, trimmed and deduped. None means "not specified"
    (leave untouched on update); "" is a valid explicit clear."""
    if collaborators is None:
        return None
    names = [n.strip() for n in collaborators.split(",") if n.strip()]
    seen: list[str] = []
    for n in names:
        if n not in seen:
            seen.append(n)
    return ", ".join(seen)


class _Cursor:
    """Thin sqlite3.Cursor-alike over a libsql ResultSet, so the rest of this
    module can keep writing conn.execute(...).fetchone()/.fetchall()/.lastrowid."""

    def __init__(self, rs: libsql_client.ResultSet):
        self._rs = rs

    def fetchone(self):
        return self._rs.rows[0] if self._rs.rows else None

    def fetchall(self):
        return list(self._rs.rows)

    @property
    def lastrowid(self):
        return self._rs.last_insert_rowid


class _Conn:
    def __init__(self, client: libsql_client.ClientSync):
        self._client = client

    def execute(self, sql: str, params=()) -> _Cursor:
        return _Cursor(self._client.execute(sql, list(params)))


# Schema/migration check is run once per target database per process — cheap
# for a local file, but each check is a network round trip against Turso, so
# it's not worth repeating on every single method call.
_schema_ready: set[str] = set()


@contextmanager
def _connect(db_path: Path):
    turso_url = os.environ.get("TURSO_DATABASE_URL")
    if turso_url:
        target = turso_url
        # Force the HTTP transport instead of the default WebSocket (Hrana)
        # one — some corporate proxies reject the WS upgrade handshake outright.
        connect_url = "https://" + turso_url.split("://", 1)[1] if "://" in turso_url else turso_url
        raw = libsql_client.create_client_sync(connect_url, auth_token=os.environ.get("TURSO_AUTH_TOKEN"))
    else:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        target = f"file:{db_path}"
        raw = libsql_client.create_client_sync(target)
    conn = _Conn(raw)
    try:
        if target not in _schema_ready:
            if not turso_url:
                conn.execute("PRAGMA journal_mode=WAL")
            for stmt in SCHEMA_STATEMENTS:
                conn.execute(stmt)
            cols = [r[1] for r in conn.execute("PRAGMA table_info(tasks)").fetchall()]
            for col, sqltype in _MIGRATED_COLUMNS.items():
                if col not in cols:
                    conn.execute(f"ALTER TABLE tasks ADD COLUMN {col} {sqltype}")
            if "sort_order" not in cols:
                conn.execute("UPDATE tasks SET sort_order = id * 10 WHERE sort_order IS NULL")
            # "not mentioned" should default to a real state, for old rows and new ones alike —
            # idempotent, cheap enough to just run once per schema-ready target.
            conn.execute("UPDATE tasks SET execution_state = 'Not started' WHERE execution_state IS NULL OR execution_state = ''")
            conn.execute("UPDATE tasks SET custom_fields = '{}' WHERE custom_fields IS NULL OR custom_fields = ''")
            _schema_ready.add(target)
        yield conn
    finally:
        raw.close()


@dataclass
class Task:
    id: int
    track: str
    module: str | None
    owner: str
    collaborators: str | None
    task: str
    added: str
    due: str
    due_assumed: bool
    priority: str
    status: str
    updated_at: str
    source: str | None
    sort_order: int
    execution_state: str | None
    review_status: str | None
    reviewer: str | None
    review_type: str | None
    review_due: str | None
    review_comment: str | None
    blocked_by_id: int | None
    custom_fields: str | None
    parent_id: int | None
    start_date: str | None
    baseline_start: str | None
    baseline_end: str | None
    percent_complete: int | None
    pinned: int | None
    dependency_type: str | None
    lag_days: int | None
    is_milestone: int | None

    @classmethod
    def from_row(cls, row: libsql_client.Row) -> "Task":
        d = row.asdict()
        d["due_assumed"] = bool(d["due_assumed"])
        return cls(**d)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["custom_fields"] = json.loads(self.custom_fields) if self.custom_fields else {}
        return d


@dataclass
class HistoryEntry:
    id: int
    task_id: int
    field: str
    old_value: str | None
    new_value: str | None
    changed_by: str
    changed_at: str

    @classmethod
    def from_row(cls, row: libsql_client.Row) -> "HistoryEntry":
        return cls(**row.asdict())

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Note:
    id: int
    task_id: int
    author: str
    text: str
    created_at: str
    pinned: bool

    @classmethod
    def from_row(cls, row: libsql_client.Row) -> "Note":
        d = row.asdict()
        d["pinned"] = bool(d["pinned"])
        return cls(**d)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Meeting:
    id: int
    title: str
    start_time: str
    end_time: str | None
    organizer: str | None
    attendees: str | None
    join_url: str | None
    transcript_source: str
    transcript_text: str | None
    summary: str | None
    drive_link: str | None
    status: str
    tasks_created: bool
    created_at: str
    updated_at: str

    @classmethod
    def from_row(cls, row: libsql_client.Row) -> "Meeting":
        d = row.asdict()
        d["tasks_created"] = bool(d["tasks_created"])
        return cls(**d)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["summary"] = json.loads(self.summary) if self.summary else None
        return d


@dataclass
class Capture:
    id: int
    text: str
    summary: str | None
    created_at: str

    @classmethod
    def from_row(cls, row: libsql_client.Row) -> "Capture":
        return cls(**row.asdict())

    def to_dict(self) -> dict:
        d = asdict(self)
        d["summary"] = json.loads(self.summary) if self.summary else None
        return d


def _capture_row_to_dict(row: libsql_client.Row) -> dict:
    return Capture.from_row(row).to_dict()


class TaskStore:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)

    def add_task(
        self,
        track: str,
        owner: str,
        task: str,
        module: str | None = None,
        due: str | None = None,
        priority: str = "Normal",
        status: str = "Open",
        source: str | None = None,
        collaborators: str | None = None,
        execution_state: str | None = None,
        parent_id: int | None = None,
        start_date: str | None = None,
        percent_complete: int | None = None,
        pinned: bool = False,
        dependency_type: str | None = None,
        lag_days: int | None = None,
        is_milestone: bool = False,
    ) -> dict:
        if track not in TRACKS:
            raise TrackerError(f"track must be one of {TRACKS}, got {track!r}")
        if priority not in PRIORITIES:
            raise TrackerError(f"priority must be one of {PRIORITIES}, got {priority!r}")
        if status not in STATUSES:
            raise TrackerError(f"status must be one of {STATUSES}, got {status!r}")
        if execution_state and execution_state not in EXECUTION_STATES:
            raise TrackerError(f"execution_state must be one of {EXECUTION_STATES}, got {execution_state!r}")
        if dependency_type is not None and dependency_type not in DEPENDENCY_TYPES:
            raise TrackerError(f"dependency_type must be one of {DEPENDENCY_TYPES}, got {dependency_type!r}")
        if not owner or not owner.strip():
            raise TrackerError("owner is required")
        if not task or not task.strip():
            raise TrackerError("task is required")

        due_assumed = due is None
        due_date = due if due else end_of_work_week().isoformat()
        collaborators = _normalize_collaborators(collaborators)
        execution_state = execution_state or "Not started"  # "not mentioned" defaults to a real state

        now = _now_ist()
        with _connect(self.db_path) as conn:
            if parent_id is not None:
                parent = conn.execute("SELECT id FROM tasks WHERE id = ?", (parent_id,)).fetchone()
                if parent is None:
                    raise TrackerError(f"no task with id {parent_id} to use as parent")
            max_order = conn.execute("SELECT COALESCE(MAX(sort_order), 0) FROM tasks").fetchone()[0]
            cur = conn.execute(
                """INSERT INTO tasks
                   (track, module, owner, collaborators, task, added, due, due_assumed, priority, status,
                    updated_at, source, sort_order, execution_state, custom_fields,
                    parent_id, start_date, percent_complete, pinned, dependency_type, lag_days, is_milestone)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    track, module, owner.strip(), collaborators, task.strip(),
                    now.date().isoformat(), due_date, int(due_assumed),
                    priority, status, now.isoformat(), source, max_order + 10, execution_state, "{}",
                    parent_id, start_date, percent_complete, int(pinned), dependency_type, lag_days, int(is_milestone),
                ),
            )
            row = conn.execute("SELECT * FROM tasks WHERE id = ?", (cur.lastrowid,)).fetchone()
        return Task.from_row(row).to_dict()

    def delete_task(self, id: int) -> None:
        with _connect(self.db_path) as conn:
            existing = conn.execute("SELECT id FROM tasks WHERE id = ?", (id,)).fetchone()
            if existing is None:
                raise TrackerError(f"no task with id {id}")
            # Local SQLite doesn't enforce foreign keys by default, but Turso
            # does — delete dependents first so this works on both.
            conn.execute("DELETE FROM notes WHERE task_id = ?", (id,))
            conn.execute("DELETE FROM history WHERE task_id = ?", (id,))
            conn.execute("DELETE FROM tasks WHERE id = ?", (id,))

    def reorder(self, id_order: list[int]) -> None:
        """Assign fresh sort_order values (spaced by 10) to exactly the given
        ids, in the order given. Ids not included keep their existing order."""
        with _connect(self.db_path) as conn:
            for position, task_id in enumerate(id_order):
                conn.execute("UPDATE tasks SET sort_order = ? WHERE id = ?", (position * 10, task_id))

    def update_task(
        self,
        id: int,
        track: str | None = None,
        module: str | None = None,
        owner: str | None = None,
        collaborators: str | None = None,
        task: str | None = None,
        due: str | None = None,
        priority: str | None = None,
        status: str | None = None,
        execution_state: str | None = None,
        review_status: str | None = None,
        reviewer: str | None = None,
        review_type: str | None = None,
        review_due: str | None = None,
        review_comment: str | None = None,
        clear_review: bool = False,
        blocked_by_id: int | None = None,
        clear_blocked_by: bool = False,
        custom_fields: dict | None = None,
        parent_id: int | None = None,
        clear_parent: bool = False,
        start_date: str | None = None,
        baseline_start: str | None = None,
        baseline_end: str | None = None,
        percent_complete: int | None = None,
        pinned: bool | None = None,
        dependency_type: str | None = None,
        lag_days: int | None = None,
        is_milestone: bool | None = None,
        changed_by: str | None = None,
    ) -> dict:
        if track is not None and track not in TRACKS:
            raise TrackerError(f"track must be one of {TRACKS}, got {track!r}")
        if priority is not None and priority not in PRIORITIES:
            raise TrackerError(f"priority must be one of {PRIORITIES}, got {priority!r}")
        if status is not None and status not in STATUSES:
            raise TrackerError(f"status must be one of {STATUSES}, got {status!r}")
        if execution_state and execution_state not in EXECUTION_STATES:
            raise TrackerError(f"execution_state must be one of {EXECUTION_STATES}, got {execution_state!r}")
        if review_status is not None and review_status not in REVIEW_STATUSES:
            raise TrackerError(f"review_status must be one of {REVIEW_STATUSES}, got {review_status!r}")
        if review_type is not None and review_type not in REVIEW_TYPES:
            raise TrackerError(f"review_type must be one of {REVIEW_TYPES}, got {review_type!r}")
        if blocked_by_id is not None and blocked_by_id == id:
            raise TrackerError("a task cannot be blocked by itself")
        if dependency_type is not None and dependency_type not in DEPENDENCY_TYPES:
            raise TrackerError(f"dependency_type must be one of {DEPENDENCY_TYPES}, got {dependency_type!r}")
        if parent_id is not None and parent_id == id:
            raise TrackerError("a task cannot be its own parent")

        fields = {"track": track, "module": module, "owner": owner,
                  "collaborators": _normalize_collaborators(collaborators), "task": task,
                  "priority": priority, "status": status, "execution_state": execution_state,
                  "review_status": review_status, "reviewer": reviewer, "review_type": review_type,
                  "review_due": review_due, "review_comment": review_comment,
                  "blocked_by_id": blocked_by_id, "parent_id": parent_id,
                  "start_date": start_date, "baseline_start": baseline_start, "baseline_end": baseline_end,
                  "percent_complete": percent_complete, "dependency_type": dependency_type, "lag_days": lag_days}
        fields = {k: v for k, v in fields.items() if v is not None}
        if pinned is not None:
            fields["pinned"] = int(pinned)
        if is_milestone is not None:
            fields["is_milestone"] = int(is_milestone)
        if due is not None:
            fields["due"] = due
            fields["due_assumed"] = 0
        if clear_review:
            fields.update({"review_status": None, "reviewer": None, "review_type": None,
                           "review_due": None, "review_comment": None})
        if clear_blocked_by:
            fields["blocked_by_id"] = None
        if clear_parent:
            fields["parent_id"] = None

        with _connect(self.db_path) as conn:
            existing = conn.execute("SELECT * FROM tasks WHERE id = ?", (id,)).fetchone()
            if existing is None:
                raise TrackerError(f"no task with id {id}")
            if blocked_by_id is not None:
                blocker = conn.execute("SELECT id FROM tasks WHERE id = ?", (blocked_by_id,)).fetchone()
                if blocker is None:
                    raise TrackerError(f"no task with id {blocked_by_id} to block on")
                # The CPM scheduler assumes the blocked_by_id chain is a DAG (in fact a
                # forest, one predecessor per task) — walk it to reject a cycle up front.
                cursor_id, depth = blocked_by_id, 0
                while cursor_id is not None and depth < 1000:
                    if cursor_id == id:
                        raise TrackerError("cannot set blocked_by_id: would create a dependency cycle")
                    row = conn.execute("SELECT blocked_by_id FROM tasks WHERE id = ?", (cursor_id,)).fetchone()
                    cursor_id = row["blocked_by_id"] if row else None
                    depth += 1
            if parent_id is not None:
                parent = conn.execute("SELECT id FROM tasks WHERE id = ?", (parent_id,)).fetchone()
                if parent is None:
                    raise TrackerError(f"no task with id {parent_id} to use as parent")
                # Walk up the proposed parent's ancestor chain — if `id` appears in it,
                # reparenting would create a cycle (e.g. making a task a subtask of its own subtask).
                cursor_id, depth = parent_id, 0
                while cursor_id is not None and depth < 1000:
                    if cursor_id == id:
                        raise TrackerError("cannot set parent: would create a cycle")
                    row = conn.execute("SELECT parent_id FROM tasks WHERE id = ?", (cursor_id,)).fetchone()
                    cursor_id = row["parent_id"] if row else None
                    depth += 1
            if custom_fields:
                merged = json.loads(existing["custom_fields"] or "{}")
                merged.update(custom_fields)
                fields["custom_fields"] = json.dumps(merged)

            if not fields:
                raise TrackerError("no fields to update")

            # Audit trail: one history row per field that's actually changing, skipping the
            # due_assumed side-effect (it's not a field anyone edits directly) and no-op writes
            # (e.g. re-selecting the same value) so the log stays meaningful, not noisy.
            now_iso = _now_ist().isoformat()
            history_rows = []
            for key, new_val in fields.items():
                if key == "due_assumed":
                    continue
                old_val = existing[key]
                if str(old_val) == str(new_val):
                    continue
                history_rows.append((id, key, old_val, new_val, changed_by or "Unknown", now_iso))

            fields["updated_at"] = now_iso
            set_clause = ", ".join(f"{k} = ?" for k in fields)
            conn.execute(f"UPDATE tasks SET {set_clause} WHERE id = ?", (*fields.values(), id))
            for h in history_rows:
                conn.execute(
                    "INSERT INTO history (task_id, field, old_value, new_value, changed_by, changed_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    h,
                )
            row = conn.execute("SELECT * FROM tasks WHERE id = ?", (id,)).fetchone()
        return Task.from_row(row).to_dict()

    def list_history(self, task_id: int) -> list[dict]:
        with _connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT * FROM history WHERE task_id = ? ORDER BY changed_at DESC", (task_id,)
            ).fetchall()
        return [HistoryEntry.from_row(r).to_dict() for r in rows]

    def list_recent_history(
        self, since: str | None = None, until: str | None = None, limit: int = 200,
    ) -> list[dict]:
        """Across all tasks — powers the activity feed and the sidebar's
        day-by-day activity browser (since+until bound a single day's window)."""
        with _connect(self.db_path) as conn:
            query = (
                "SELECT h.*, t.task AS task_title FROM history h JOIN tasks t ON t.id = h.task_id"
            )
            clauses, params = [], []
            if since:
                clauses.append("h.changed_at > ?")
                params.append(since)
            if until:
                clauses.append("h.changed_at <= ?")
                params.append(until)
            if clauses:
                query += " WHERE " + " AND ".join(clauses)
            query += " ORDER BY h.changed_at DESC LIMIT ?"
            params.append(limit)
            rows = conn.execute(query, params).fetchall()
        return [r.asdict() for r in rows]

    def list_tasks(
        self,
        owner: str | None = None,
        track: str | None = None,
        module: str | None = None,
        status: str | None = None,
        priority: str | None = None,
        overdue_only: bool = False,
        due_this_week: bool = False,
        query: str | None = None,
    ) -> list[dict]:
        clauses, params = [], []
        if owner:
            # Matches collaborators too — a name filter for Sparsh should also surface
            # a task where he's a collaborator (e.g. owner=Abhishek, collaborators=Sparsh),
            # not just tasks where he's the primary owner.
            clauses.append("(owner LIKE ? OR collaborators LIKE ?)")
            params.extend([f"%{owner}%", f"%{owner}%"])
        if track:
            clauses.append("track = ?")
            params.append(track)
        if module:
            clauses.append("module LIKE ?")
            params.append(f"%{module}%")
        if status:
            clauses.append("status = ?")
            params.append(status)
        if priority:
            clauses.append("priority = ?")
            params.append(priority)
        if query:
            clauses.append("(task LIKE ? OR owner LIKE ? OR module LIKE ?)")
            params.extend([f"%{query}%"] * 3)

        today = _today_ist()
        if overdue_only:
            clauses.append("due < ? AND status = 'Open'")
            params.append(today.isoformat())
        if due_this_week:
            monday = today - timedelta(days=today.weekday())
            sunday = monday + timedelta(days=6)
            clauses.append("due BETWEEN ? AND ?")
            params.extend([monday.isoformat(), sunday.isoformat()])

        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with _connect(self.db_path) as conn:
            rows = conn.execute(
                f"SELECT * FROM tasks {where} ORDER BY sort_order ASC",
                params,
            ).fetchall()
        return [Task.from_row(r).to_dict() for r in rows]

    def add_note(self, task_id: int, author: str, text: str) -> dict:
        if not text or not text.strip():
            raise TrackerError("note text is required")
        now = _now_ist().isoformat()
        with _connect(self.db_path) as conn:
            existing = conn.execute("SELECT id FROM tasks WHERE id = ?", (task_id,)).fetchone()
            if existing is None:
                raise TrackerError(f"no task with id {task_id}")
            cur = conn.execute(
                "INSERT INTO notes (task_id, author, text, created_at, pinned) VALUES (?, ?, ?, ?, 0)",
                (task_id, author, text.strip(), now),
            )
            row = conn.execute("SELECT * FROM notes WHERE id = ?", (cur.lastrowid,)).fetchone()
        return Note.from_row(row).to_dict()

    def list_notes(self, task_id: int) -> list[dict]:
        with _connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT * FROM notes WHERE task_id = ? ORDER BY pinned DESC, created_at ASC", (task_id,)
            ).fetchall()
        return [Note.from_row(r).to_dict() for r in rows]

    def note_counts(self) -> dict[int, int]:
        with _connect(self.db_path) as conn:
            rows = conn.execute("SELECT task_id, COUNT(*) as n FROM notes GROUP BY task_id").fetchall()
        return {r["task_id"]: r["n"] for r in rows}

    def note_summaries(self) -> dict[int, dict]:
        """Per task: count + the note to preview (pinned note wins, else most recent), including
        that note's own id/author so the dashboard's inline field knows what it's showing and
        can offer to edit it in place rather than only ever adding a new note."""
        with _connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT id, task_id, text, author, pinned, created_at FROM notes ORDER BY pinned DESC, created_at DESC"
            ).fetchall()
        summaries: dict[int, dict] = {}
        for r in rows:
            s = summaries.setdefault(
                r["task_id"],
                {"count": 0, "latest": r["text"], "latest_id": r["id"], "latest_author": r["author"]},
            )
            s["count"] += 1
        return summaries

    def edit_note(self, note_id: int, author: str, text: str) -> dict:
        if not text or not text.strip():
            raise TrackerError("note text is required")
        with _connect(self.db_path) as conn:
            row = conn.execute("SELECT * FROM notes WHERE id = ?", (note_id,)).fetchone()
            if row is None:
                raise TrackerError(f"no note with id {note_id}")
            if row["author"] != author:
                raise TrackerError("can only edit your own note")
            conn.execute("UPDATE notes SET text = ? WHERE id = ?", (text.strip(), note_id))
            row = conn.execute("SELECT * FROM notes WHERE id = ?", (note_id,)).fetchone()
        return Note.from_row(row).to_dict()

    def delete_note(self, note_id: int, author: str | None = None) -> None:
        with _connect(self.db_path) as conn:
            row = conn.execute("SELECT * FROM notes WHERE id = ?", (note_id,)).fetchone()
            if row is None:
                raise TrackerError(f"no note with id {note_id}")
            if author is not None and row["author"] != author:
                raise TrackerError("can only delete your own note")
            conn.execute("DELETE FROM notes WHERE id = ?", (note_id,))

    def toggle_pin_note(self, note_id: int) -> dict:
        with _connect(self.db_path) as conn:
            row = conn.execute("SELECT * FROM notes WHERE id = ?", (note_id,)).fetchone()
            if row is None:
                raise TrackerError(f"no note with id {note_id}")
            conn.execute("UPDATE notes SET pinned = ? WHERE id = ?", (0 if row["pinned"] else 1, note_id))
            row = conn.execute("SELECT * FROM notes WHERE id = ?", (note_id,)).fetchone()
        return Note.from_row(row).to_dict()

    def is_email_processed(self, entry_id: str) -> bool:
        with _connect(self.db_path) as conn:
            row = conn.execute("SELECT 1 FROM processed_emails WHERE entry_id = ?", (entry_id,)).fetchone()
        return row is not None

    def mark_email_processed(self, entry_id: str, subject: str) -> None:
        with _connect(self.db_path) as conn:
            conn.execute(
                "INSERT OR IGNORE INTO processed_emails (entry_id, subject, processed_at) VALUES (?, ?, ?)",
                (entry_id, subject, _now_ist().isoformat()),
            )

    def summarize(self, period: str = "weekly", track: str | None = None) -> dict:
        if period not in ("daily", "weekly"):
            raise TrackerError("period must be 'daily' or 'weekly'")

        window_days = 1 if period == "daily" else 7
        today = _today_ist()
        window_start = (_now_ist() - timedelta(days=window_days)).isoformat()

        track_clause = "AND track = ?" if track else ""
        track_param = [track] if track else []

        with _connect(self.db_path) as conn:
            def q(sql, extra=()):
                return [Task.from_row(r).to_dict() for r in conn.execute(sql, (*track_param, *extra)).fetchall()]

            overdue = q(f"SELECT * FROM tasks WHERE status='Open' AND due < ? {track_clause}", (today.isoformat(),))
            open_tasks = q(f"SELECT * FROM tasks WHERE status='Open' {track_clause}")
            due_soon = q(
                f"SELECT * FROM tasks WHERE status='Open' AND due BETWEEN ? AND ? {track_clause}",
                (today.isoformat(), (today + timedelta(days=window_days)).isoformat()),
            )
            completed_recently = q(
                f"SELECT * FROM tasks WHERE status='Done' AND updated_at >= ? {track_clause}",
                (window_start,),
            )

            by_owner: dict[str, dict] = {}
            for t in open_tasks:
                o = by_owner.setdefault(t["owner"], {"open": 0, "overdue": 0})
                o["open"] += 1
            for t in overdue:
                by_owner.setdefault(t["owner"], {"open": 0, "overdue": 0})["overdue"] += 1

        return {
            "period": period,
            "as_of": _now_ist().isoformat(),
            "track_filter": track,
            "total_open": len(open_tasks),
            "total_overdue": len(overdue),
            "overdue": overdue,
            "due_soon": due_soon,
            "completed_recently": completed_recently,
            "by_owner": by_owner,
        }

    def get_setting(self, key: str) -> str | None:
        with _connect(self.db_path) as conn:
            row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None

    def set_setting(self, key: str, value: str) -> None:
        with _connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
                (key, value, _now_ist().isoformat()),
            )

    # ---------- Capture history (every paste-to-parse submission, kept for scrollback) ----------

    def add_capture(self, text: str, summary: list | str | None = None) -> dict:
        if not text or not text.strip():
            raise TrackerError("text is required")
        summary_json = json.dumps(summary) if isinstance(summary, list) else summary
        now = _now_ist().isoformat()
        with _connect(self.db_path) as conn:
            cur = conn.execute(
                "INSERT INTO captures (text, summary, created_at) VALUES (?, ?, ?)",
                (text, summary_json, now),
            )
            row = conn.execute("SELECT * FROM captures WHERE id = ?", (cur.lastrowid,)).fetchone()
        return _capture_row_to_dict(row)

    def list_captures(self, limit: int = 100) -> list[dict]:
        with _connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT * FROM captures ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [_capture_row_to_dict(r) for r in rows]

    # ---------- Meetings (populated by a separate local watcher/transcriber
    # process connecting to this same database directly) ----------

    def add_meeting(
        self,
        title: str,
        start_time: str,
        organizer: str | None = None,
        attendees: str | None = None,
        join_url: str | None = None,
        transcript_source: str = "local_whisper",
        status: str = "pending",
    ) -> dict:
        if status not in MEETING_STATUSES:
            raise TrackerError(f"status must be one of {MEETING_STATUSES}, got {status!r}")
        if not title or not title.strip():
            raise TrackerError("title is required")
        now = _now_ist().isoformat()
        with _connect(self.db_path) as conn:
            cur = conn.execute(
                """INSERT INTO meetings
                   (title, start_time, organizer, attendees, join_url, transcript_source,
                    status, tasks_created, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, ?)""",
                (title.strip(), start_time, organizer, attendees, join_url,
                 transcript_source, status, now, now),
            )
            row = conn.execute("SELECT * FROM meetings WHERE id = ?", (cur.lastrowid,)).fetchone()
        return Meeting.from_row(row).to_dict()

    def update_meeting(
        self,
        id: int,
        end_time: str | None = None,
        transcript_source: str | None = None,
        transcript_text: str | None = None,
        summary: dict | str | None = None,
        drive_link: str | None = None,
        status: str | None = None,
    ) -> dict:
        if status is not None and status not in MEETING_STATUSES:
            raise TrackerError(f"status must be one of {MEETING_STATUSES}, got {status!r}")

        fields = {
            "end_time": end_time, "transcript_source": transcript_source,
            "transcript_text": transcript_text, "drive_link": drive_link, "status": status,
        }
        fields = {k: v for k, v in fields.items() if v is not None}
        if summary is not None:
            fields["summary"] = json.dumps(summary) if isinstance(summary, dict) else summary

        if not fields:
            raise TrackerError("no fields to update")
        fields["updated_at"] = _now_ist().isoformat()

        with _connect(self.db_path) as conn:
            existing = conn.execute("SELECT id FROM meetings WHERE id = ?", (id,)).fetchone()
            if existing is None:
                raise TrackerError(f"no meeting with id {id}")
            set_clause = ", ".join(f"{k} = ?" for k in fields)
            conn.execute(f"UPDATE meetings SET {set_clause} WHERE id = ?", (*fields.values(), id))
            row = conn.execute("SELECT * FROM meetings WHERE id = ?", (id,)).fetchone()
        return Meeting.from_row(row).to_dict()

    def list_meetings(self, limit: int = 50) -> list[dict]:
        with _connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT * FROM meetings ORDER BY start_time DESC LIMIT ?", (limit,)
            ).fetchall()
        return [Meeting.from_row(r).to_dict() for r in rows]

    def get_meeting(self, id: int) -> dict | None:
        with _connect(self.db_path) as conn:
            row = conn.execute("SELECT * FROM meetings WHERE id = ?", (id,)).fetchone()
        return Meeting.from_row(row).to_dict() if row else None

    def sync_meeting_tasks(self) -> list[dict]:
        """Turn any newly-'done' meeting's next_steps into real tasks, tagged with a
        source identifying which meeting they came from. Safe to call repeatedly —
        tasks_created gates it so a meeting's next_steps are only ever applied once."""
        with _connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT * FROM meetings WHERE status = 'done' AND tasks_created = 0"
            ).fetchall()

        created = []
        for row in rows:
            m = Meeting.from_row(row).to_dict()
            next_steps = (m.get("summary") or {}).get("next_steps") or []
            date_str = (m["start_time"] or "")[:10]
            source_tag = f"Meeting: {m['title']} ({date_str})"
            for step in next_steps:
                try:
                    created.append(self.add_task(
                        track=step["track"],
                        owner=step["owner"],
                        task=step["task"],
                        module=step.get("module"),
                        due=step.get("due"),
                        priority=step.get("priority", "Normal"),
                        source=source_tag,
                    ))
                except (TrackerError, KeyError):
                    continue  # skip a malformed next_step rather than fail the whole sync
            with _connect(self.db_path) as conn:
                conn.execute("UPDATE meetings SET tasks_created = 1 WHERE id = ?", (m["id"],))
        return created
