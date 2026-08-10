"""Tests for TaskStore (db.py). Run with:
    .venv/Scripts/python.exe -m pytest tools/pmo_tracker/test_db.py
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from db import TrackerError, TaskStore, end_of_work_week


@pytest.fixture(autouse=True)
def no_turso(monkeypatch):
    """Tests must stay isolated to a throwaway local file, never the real
    shared database — regardless of what TURSO_DATABASE_URL is set to in .env."""
    monkeypatch.delenv("TURSO_DATABASE_URL", raising=False)
    monkeypatch.delenv("TURSO_AUTH_TOKEN", raising=False)


@pytest.fixture
def store(tmp_path):
    return TaskStore(tmp_path / "tasks.db")


def test_add_task_minimal(store):
    t = store.add_task(track="Discovery", owner="Aayushi", task="Do the thing")
    assert t["id"] > 0
    assert t["owner"] == "Aayushi"
    assert t["status"] == "Open"
    assert t["priority"] == "Normal"
    assert t["collaborators"] is None
    assert t["execution_state"] == "Not started"
    assert t["custom_fields"] == {}
    assert t["blocked_by_id"] is None


def test_add_task_explicit_execution_state(store):
    t = store.add_task(track="Discovery", owner="Aayushi", task="X", execution_state="Blocked")
    assert t["execution_state"] == "Blocked"


def test_add_task_rejects_bad_execution_state(store):
    with pytest.raises(TrackerError):
        store.add_task(track="Discovery", owner="Aayushi", task="X", execution_state="Nope")


def test_add_task_due_defaults_to_end_of_work_week(store):
    t = store.add_task(track="Discovery", owner="Aayushi", task="Do the thing")
    assert t["due"] == end_of_work_week().isoformat()
    assert t["due_assumed"] is True


def test_add_task_explicit_due_not_assumed(store):
    t = store.add_task(track="Discovery", owner="Aayushi", task="X", due="2026-08-20")
    assert t["due"] == "2026-08-20"
    assert t["due_assumed"] is False


def test_add_task_requires_owner_and_task(store):
    with pytest.raises(TrackerError):
        store.add_task(track="Discovery", owner="  ", task="X")
    with pytest.raises(TrackerError):
        store.add_task(track="Discovery", owner="Aayushi", task="  ")


def test_add_task_rejects_bad_enum_values(store):
    with pytest.raises(TrackerError):
        store.add_task(track="NotATrack", owner="Aayushi", task="X")
    with pytest.raises(TrackerError):
        store.add_task(track="Discovery", owner="Aayushi", task="X", priority="Urgent")


def test_add_task_normalizes_collaborators(store):
    t = store.add_task(track="Discovery", owner="Sparsh", task="X", collaborators=" Abhishek, Abhishek ,Hriday ")
    assert t["collaborators"] == "Abhishek, Hriday"


def test_update_task_fields(store):
    t = store.add_task(track="Discovery", owner="Aayushi", task="X")
    updated = store.update_task(id=t["id"], status="Done", priority="High")
    assert updated["status"] == "Done"
    assert updated["priority"] == "High"


def test_update_task_due_clears_assumed_flag(store):
    t = store.add_task(track="Discovery", owner="Aayushi", task="X")
    assert t["due_assumed"] is True
    updated = store.update_task(id=t["id"], due="2026-09-01")
    assert updated["due"] == "2026-09-01"
    assert updated["due_assumed"] is False


def test_update_task_can_clear_collaborators(store):
    t = store.add_task(track="Discovery", owner="Sparsh", task="X", collaborators="Abhishek")
    assert t["collaborators"] == "Abhishek"
    updated = store.update_task(id=t["id"], collaborators="")
    assert updated["collaborators"] == ""


def test_update_task_can_clear_execution_state(store):
    t = store.add_task(track="Discovery", owner="Sparsh", task="X")
    updated = store.update_task(id=t["id"], execution_state="Blocked")
    assert updated["execution_state"] == "Blocked"
    cleared = store.update_task(id=t["id"], execution_state="")
    assert cleared["execution_state"] == ""


def test_update_task_rejects_bad_execution_state(store):
    t = store.add_task(track="Discovery", owner="Sparsh", task="X")
    with pytest.raises(TrackerError):
        store.update_task(id=t["id"], execution_state="Done-ish")


def test_update_task_unknown_id_raises(store):
    with pytest.raises(TrackerError):
        store.update_task(id=9999, status="Done")


def test_update_task_no_fields_raises(store):
    t = store.add_task(track="Discovery", owner="Aayushi", task="X")
    with pytest.raises(TrackerError):
        store.update_task(id=t["id"])


def test_delete_task(store):
    t = store.add_task(track="Discovery", owner="Aayushi", task="X")
    store.delete_task(t["id"])
    assert store.list_tasks() == []
    with pytest.raises(TrackerError):
        store.delete_task(t["id"])


def test_delete_task_also_deletes_its_notes(store):
    """Local SQLite doesn't enforce foreign keys by default, but Turso does —
    deleting a task with notes/history must not leave them (or fail)."""
    t = store.add_task(track="Discovery", owner="Aayushi", task="X")
    store.add_note(t["id"], author="Akshit", text="Some note")
    store.update_task(id=t["id"], status="Done", changed_by="Akshit")  # creates a history row too
    store.delete_task(t["id"])
    assert store.list_notes(t["id"]) == []
    assert store.list_history(t["id"]) == []


def test_list_tasks_filters_by_owner_and_status(store):
    store.add_task(track="Discovery", owner="Aayushi", task="A")
    t2 = store.add_task(track="Discovery", owner="Sparsh", task="B")
    store.update_task(id=t2["id"], status="Done")

    assert len(store.list_tasks(owner="Aayushi")) == 1
    assert len(store.list_tasks(status="Open")) == 1
    assert len(store.list_tasks(status="Done")) == 1


def test_list_tasks_owner_filter_matches_collaborators_too(store):
    store.add_task(track="Discovery", owner="Sparsh", task="A", collaborators="Abhishek")
    store.add_task(track="Discovery", owner="Aayushi", task="B")

    sparsh_tasks = store.list_tasks(owner="Sparsh")
    abhishek_tasks = store.list_tasks(owner="Abhishek")
    assert len(sparsh_tasks) == 1
    assert len(abhishek_tasks) == 1
    assert sparsh_tasks[0]["id"] == abhishek_tasks[0]["id"]


def test_reorder_sets_sort_order(store):
    a = store.add_task(track="Discovery", owner="Aayushi", task="A")
    b = store.add_task(track="Discovery", owner="Aayushi", task="B")
    store.reorder([b["id"], a["id"]])
    ordered = store.list_tasks()
    assert [t["id"] for t in ordered] == [b["id"], a["id"]]


def test_notes_crud(store):
    t = store.add_task(track="Discovery", owner="Aayushi", task="X")
    note = store.add_note(t["id"], author="Akshit", text="First note")
    assert store.list_notes(t["id"]) == [note]

    pinned = store.toggle_pin_note(note["id"])
    assert pinned["pinned"] is True

    with pytest.raises(TrackerError):
        store.delete_note(note["id"], author="SomeoneElse")
    store.delete_note(note["id"], author="Akshit")
    assert store.list_notes(t["id"]) == []


def test_add_note_requires_text_and_valid_task(store):
    t = store.add_task(track="Discovery", owner="Aayushi", task="X")
    with pytest.raises(TrackerError):
        store.add_note(t["id"], author="Akshit", text="  ")
    with pytest.raises(TrackerError):
        store.add_note(9999, author="Akshit", text="hi")


def test_edit_note_updates_text(store):
    t = store.add_task(track="Discovery", owner="Aayushi", task="X")
    note = store.add_note(t["id"], author="Akshit", text="Original")
    updated = store.edit_note(note["id"], author="Akshit", text="Revised")
    assert updated["text"] == "Revised"
    assert store.list_notes(t["id"])[0]["text"] == "Revised"


def test_edit_note_rejects_other_authors_and_blank_text(store):
    t = store.add_task(track="Discovery", owner="Aayushi", task="X")
    note = store.add_note(t["id"], author="Akshit", text="Original")
    with pytest.raises(TrackerError):
        store.edit_note(note["id"], author="SomeoneElse", text="Hijacked")
    with pytest.raises(TrackerError):
        store.edit_note(note["id"], author="Akshit", text="   ")
    with pytest.raises(TrackerError):
        store.edit_note(9999, author="Akshit", text="hi")


def test_note_summaries_include_latest_id_and_author(store):
    t = store.add_task(track="Discovery", owner="Aayushi", task="X")
    note = store.add_note(t["id"], author="Akshit", text="Only note")
    summaries = store.note_summaries()
    assert summaries[t["id"]]["latest_id"] == note["id"]
    assert summaries[t["id"]]["latest_author"] == "Akshit"


# ---------- Audit trail ----------

def test_update_task_logs_history(store):
    t = store.add_task(track="Discovery", owner="Aayushi", task="X")
    store.update_task(id=t["id"], status="Done", priority="High", changed_by="Akshit")
    history = store.list_history(t["id"])
    fields_changed = {h["field"] for h in history}
    assert fields_changed == {"status", "priority"}
    status_entry = next(h for h in history if h["field"] == "status")
    assert status_entry["old_value"] == "Open"
    assert status_entry["new_value"] == "Done"
    assert status_entry["changed_by"] == "Akshit"


def test_update_task_skips_history_for_unchanged_values(store):
    t = store.add_task(track="Discovery", owner="Aayushi", task="X", priority="High")
    store.update_task(id=t["id"], priority="High", changed_by="Akshit")  # re-selecting same value
    assert store.list_history(t["id"]) == []


def test_update_task_without_changed_by_logs_unknown(store):
    t = store.add_task(track="Discovery", owner="Aayushi", task="X")
    store.update_task(id=t["id"], status="Done")
    assert store.list_history(t["id"])[0]["changed_by"] == "Unknown"


def test_list_recent_history_across_tasks(store):
    t1 = store.add_task(track="Discovery", owner="Aayushi", task="A")
    t2 = store.add_task(track="Discovery", owner="Sparsh", task="B")
    store.update_task(id=t1["id"], status="Done", changed_by="Akshit")
    store.update_task(id=t2["id"], priority="High", changed_by="Akshit")
    recent = store.list_recent_history()
    assert len(recent) == 2
    assert {r["task_title"] for r in recent} == {"A", "B"}


def test_list_recent_history_bounded_by_until(store):
    t = store.add_task(track="Discovery", owner="Aayushi", task="A")
    store.update_task(id=t["id"], status="Done", changed_by="Akshit")
    changed_at = store.list_history(t["id"])[0]["changed_at"]
    assert store.list_recent_history(until=changed_at) == store.list_recent_history()
    assert store.list_recent_history(since=changed_at) == []


# ---------- Dependencies (blocked_by_id) ----------

def test_set_and_clear_blocked_by(store):
    blocker = store.add_task(track="Discovery", owner="Aayushi", task="Blocker")
    blocked = store.add_task(track="Discovery", owner="Sparsh", task="Blocked")
    updated = store.update_task(id=blocked["id"], blocked_by_id=blocker["id"])
    assert updated["blocked_by_id"] == blocker["id"]
    cleared = store.update_task(id=blocked["id"], clear_blocked_by=True)
    assert cleared["blocked_by_id"] is None


def test_blocked_by_rejects_self_and_missing_task(store):
    t = store.add_task(track="Discovery", owner="Aayushi", task="X")
    with pytest.raises(TrackerError):
        store.update_task(id=t["id"], blocked_by_id=t["id"])
    with pytest.raises(TrackerError):
        store.update_task(id=t["id"], blocked_by_id=9999)


# ---------- Custom fields ----------

def test_custom_fields_merge_not_replace(store):
    t = store.add_task(track="Discovery", owner="Aayushi", task="X")
    store.update_task(id=t["id"], custom_fields={"Risk": "High"})
    updated = store.update_task(id=t["id"], custom_fields={"Effort": "3d"})
    assert updated["custom_fields"] == {"Risk": "High", "Effort": "3d"}


# ---------- Settings (project-context doc) ----------

def test_get_setting_returns_none_when_unset(store):
    assert store.get_setting("project_context") is None


def test_set_and_get_setting(store):
    store.set_setting("project_context", "# Team\n- Akshit: PL")
    assert store.get_setting("project_context") == "# Team\n- Akshit: PL"


def test_set_setting_overwrites_existing_value(store):
    store.set_setting("project_context", "v1")
    store.set_setting("project_context", "v2")
    assert store.get_setting("project_context") == "v2"
