"""SQLite-compatible persistence for the PMO tracker, via libsql (Turso when
TURSO_DATABASE_URL is set, otherwise a local file — same engine either way).
No NLU here — callers (the MCP tools in server.py, driven by Claude's own
parsing of chat messages) pass already structured fields; this module only
validates, computes defaults, and persists.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from dataclasses import dataclass, asdict
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import truststore

truststore.inject_into_ssl()  # needed behind a corporate TLS-intercepting proxy; certifi's bundle won't have its cert, but Windows' own cert store does. Only matters for TURSO_DATABASE_URL (remote); local file mode doesn't use TLS.

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
]

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
}


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

    @classmethod
    def from_row(cls, row: libsql_client.Row) -> "Task":
        d = row.asdict()
        d["due_assumed"] = bool(d["due_assumed"])
        return cls(**d)

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
    ) -> dict:
        if track not in TRACKS:
            raise TrackerError(f"track must be one of {TRACKS}, got {track!r}")
        if priority not in PRIORITIES:
            raise TrackerError(f"priority must be one of {PRIORITIES}, got {priority!r}")
        if status not in STATUSES:
            raise TrackerError(f"status must be one of {STATUSES}, got {status!r}")
        if not owner or not owner.strip():
            raise TrackerError("owner is required")
        if not task or not task.strip():
            raise TrackerError("task is required")

        due_assumed = due is None
        due_date = due if due else end_of_work_week().isoformat()
        collaborators = _normalize_collaborators(collaborators)

        now = _now_ist()
        with _connect(self.db_path) as conn:
            max_order = conn.execute("SELECT COALESCE(MAX(sort_order), 0) FROM tasks").fetchone()[0]
            cur = conn.execute(
                """INSERT INTO tasks
                   (track, module, owner, collaborators, task, added, due, due_assumed, priority, status, updated_at, source, sort_order)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    track, module, owner.strip(), collaborators, task.strip(),
                    now.date().isoformat(), due_date, int(due_assumed),
                    priority, status, now.isoformat(), source, max_order + 10,
                ),
            )
            row = conn.execute("SELECT * FROM tasks WHERE id = ?", (cur.lastrowid,)).fetchone()
        return Task.from_row(row).to_dict()

    def delete_task(self, id: int) -> None:
        with _connect(self.db_path) as conn:
            existing = conn.execute("SELECT id FROM tasks WHERE id = ?", (id,)).fetchone()
            if existing is None:
                raise TrackerError(f"no task with id {id}")
            # Local SQLite doesn't enforce the notes->tasks foreign key by default,
            # but Turso does — delete notes first so this works on both.
            conn.execute("DELETE FROM notes WHERE task_id = ?", (id,))
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

        fields = {"track": track, "module": module, "owner": owner,
                  "collaborators": _normalize_collaborators(collaborators), "task": task,
                  "priority": priority, "status": status, "execution_state": execution_state,
                  "review_status": review_status, "reviewer": reviewer, "review_type": review_type,
                  "review_due": review_due, "review_comment": review_comment}
        fields = {k: v for k, v in fields.items() if v is not None}
        if due is not None:
            fields["due"] = due
            fields["due_assumed"] = 0
        if clear_review:
            fields.update({"review_status": None, "reviewer": None, "review_type": None,
                           "review_due": None, "review_comment": None})

        if not fields:
            raise TrackerError("no fields to update")

        fields["updated_at"] = _now_ist().isoformat()

        with _connect(self.db_path) as conn:
            existing = conn.execute("SELECT id FROM tasks WHERE id = ?", (id,)).fetchone()
            if existing is None:
                raise TrackerError(f"no task with id {id}")
            set_clause = ", ".join(f"{k} = ?" for k in fields)
            conn.execute(f"UPDATE tasks SET {set_clause} WHERE id = ?", (*fields.values(), id))
            row = conn.execute("SELECT * FROM tasks WHERE id = ?", (id,)).fetchone()
        return Task.from_row(row).to_dict()

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
            clauses.append("owner LIKE ?")
            params.append(f"%{owner}%")
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
