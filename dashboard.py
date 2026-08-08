"""Local editable dashboard for the PMO tracker. Runs ONLY on your machine
(Flask dev server, localhost-only) and reads/writes the same tasks.db the
chat tools and email scripts use — one source of truth.

Covers: pivoting (view by / then by), inline + group-level add rows with
paste-to-parse, a fixed/sticky completion column, Excel-style filters with
active-filter chips, bulk actions, row + column drag reorder, a column
manager, saved views, a review workflow, per-task notes, and keyboard
shortcuts. See chat for what was deliberately simplified or deferred
(review history/audit trail, note @mentions/link previews, a "created by"
column — there's no identity system across chat/email/dashboard to support
one truthfully).
"""

import os
import sys
from pathlib import Path

from flask import Flask, Response, jsonify, render_template, request

sys.path.insert(0, str(Path(__file__).parent))
from db import (  # noqa: E402
    EXECUTION_STATES, PRIORITIES, REVIEW_STATUSES, REVIEW_TYPES, STATUSES, TRACKS,
    TaskStore, TrackerError,
)
import nlu  # noqa: E402

DEFAULT_DB_PATH = Path(__file__).parent / "tasks.db"
DB_PATH = os.environ.get("PMO_TRACKER_DB_PATH", str(DEFAULT_DB_PATH))
CURRENT_USER = "Akshit"  # the one and only reviewer; always offered as an owner too
store = TaskStore(DB_PATH)

app = Flask(__name__)


@app.get("/")
def index():
    resp = Response(render_template("index.html", current_user=CURRENT_USER), mimetype="text/html")
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
    return resp


@app.get("/api/meta")
def meta():
    tasks = store.list_tasks()
    modules = sorted({t["module"] for t in tasks if t["module"]})
    collaborator_names = {
        n.strip() for t in tasks for n in (t["collaborators"] or "").split(",") if n.strip()
    }
    owners = sorted({t["owner"] for t in tasks} | collaborator_names | {CURRENT_USER})
    return jsonify({
        "tracks": list(TRACKS), "priorities": list(PRIORITIES), "statuses": list(STATUSES),
        "execution_states": list(EXECUTION_STATES), "review_statuses": list(REVIEW_STATUSES),
        "review_types": list(REVIEW_TYPES), "modules": modules, "owners": owners,
    })


@app.get("/api/tasks")
def get_tasks():
    return jsonify(store.list_tasks())


@app.get("/api/note_counts")
def note_counts():
    return jsonify(store.note_summaries())


@app.post("/api/tasks")
def create_task():
    try:
        return jsonify(store.add_task(**request.get_json(force=True)))
    except TrackerError as e:
        return jsonify({"error": str(e)}), 400


@app.patch("/api/tasks/<int:task_id>")
def edit_task(task_id):
    try:
        return jsonify(store.update_task(id=task_id, **request.get_json(force=True)))
    except TrackerError as e:
        return jsonify({"error": str(e)}), 400


@app.delete("/api/tasks/<int:task_id>")
def remove_task(task_id):
    try:
        store.delete_task(task_id)
        return jsonify({"ok": True})
    except TrackerError as e:
        return jsonify({"error": str(e)}), 400


@app.post("/api/reorder")
def reorder():
    ids = request.get_json(force=True)["ids"]
    store.reorder(ids)
    return jsonify({"ok": True})


@app.post("/api/bulk")
def bulk_update():
    body = request.get_json(force=True)
    ids, fields = body["ids"], body["fields"]
    results = []
    for task_id in ids:
        try:
            results.append(store.update_task(id=task_id, **fields))
        except TrackerError as e:
            results.append({"id": task_id, "error": str(e)})
    return jsonify(results)


@app.post("/api/parse")
def parse_text():
    body = request.get_json(force=True)
    text, defaults = body.get("text", ""), body.get("defaults", {})
    try:
        client = nlu.get_client()
        open_tasks = store.list_tasks(status="Open")
        result = nlu.call_claude(client, text, open_tasks, defaults=defaults)
        changes, results = nlu.apply_actions(store, result.get("actions", []))
        return jsonify({"changes": changes, "results": results, "notes": result.get("notes", "")})
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@app.get("/api/tasks/<int:task_id>/notes")
def get_notes(task_id):
    return jsonify(store.list_notes(task_id))


@app.post("/api/tasks/<int:task_id>/notes")
def add_note(task_id):
    body = request.get_json(force=True)
    try:
        return jsonify(store.add_note(task_id, body["author"], body["text"]))
    except TrackerError as e:
        return jsonify({"error": str(e)}), 400


@app.patch("/api/notes/<int:note_id>")
def edit_note(note_id):
    body = request.get_json(force=True)
    try:
        return jsonify(store.edit_note(note_id, body["author"], body["text"]))
    except TrackerError as e:
        return jsonify({"error": str(e)}), 400


@app.delete("/api/notes/<int:note_id>")
def remove_note(note_id):
    author = request.args.get("author")
    try:
        store.delete_note(note_id, author=author)
        return jsonify({"ok": True})
    except TrackerError as e:
        return jsonify({"error": str(e)}), 400


@app.post("/api/notes/<int:note_id>/pin")
def pin_note(note_id):
    try:
        return jsonify(store.toggle_pin_note(note_id))
    except TrackerError as e:
        return jsonify({"error": str(e)}), 400


if __name__ == "__main__":
    app.run(port=int(os.environ.get("PMO_TRACKER_PORT", 5057)), debug=False)
