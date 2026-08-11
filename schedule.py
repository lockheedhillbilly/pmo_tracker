"""Server-side port of static/gantt.js's CPM engine — same forward/backward pass, same
single-predecessor-forest assumption (each task has at most one blocked_by_id, so the
forward pass never merges multiple predecessors and the backward pass only ever takes a
min over successors). Kept in lockstep with gantt.js by design: any behavior change there
should be mirrored here. Used server-side for the Excel export's Timeline/Critical
Path/Risks sheets, which need slack/criticality that isn't itself persisted in the DB.
"""

from __future__ import annotations

from datetime import date, timedelta

DependencyType = str  # "FS" | "SS" | "FF" | "SF"


def _parse(d: str | None, fallback: date | None = None) -> date | None:
    if not d:
        return fallback
    return date.fromisoformat(d)


def _task_dates(t: dict) -> tuple[date, date]:
    end = _parse(t.get("due"))
    start = _parse(t.get("start_date"), fallback=end)
    if t.get("is_milestone"):
        return start, start
    return start, end


def _duration(t: dict) -> int:
    start, end = _task_dates(t)
    if not start or not end:
        return 0
    return max(0, (end - start).days)


def _forward_constraint(dep_type: str, lag: int, pred_start: date, pred_finish: date, dur: int) -> tuple[date, date]:
    if dep_type == "SS":
        start = pred_start + timedelta(days=lag)
        return start, start + timedelta(days=dur)
    if dep_type == "FF":
        finish = pred_finish + timedelta(days=lag)
        return finish - timedelta(days=dur), finish
    if dep_type == "SF":
        finish = pred_start + timedelta(days=lag)
        return finish - timedelta(days=dur), finish
    start = pred_finish + timedelta(days=lag)  # FS (default)
    return start, start + timedelta(days=dur)


def _backward_constraint(dep_type: str, lag: int, succ_latest_start: date, succ_latest_finish: date, dur: int) -> date:
    if dep_type == "SS":
        return succ_latest_start - timedelta(days=lag) + timedelta(days=dur)
    if dep_type == "FF":
        return succ_latest_finish - timedelta(days=lag)
    if dep_type == "SF":
        return succ_latest_finish - timedelta(days=lag) + timedelta(days=dur)
    return succ_latest_start - timedelta(days=lag)  # FS (default)


def compute_schedule(tasks: list[dict]) -> dict[int, dict]:
    """Mirrors gantt.js's computeSchedule(). Returns {id: {start, finish, latest_start,
    latest_finish, slack_days, critical, duration_days}} with dates as `date` objects."""
    by_id = {t["id"]: t for t in tasks}
    successors: dict[int, list[int]] = {t["id"]: [] for t in tasks}
    for t in tasks:
        pred_id = t.get("blocked_by_id")
        if pred_id is not None and pred_id in by_id:
            successors[pred_id].append(t["id"])

    def has_predecessor(t):
        pred_id = t.get("blocked_by_id")
        return pred_id is not None and pred_id in by_id

    roots = [t["id"] for t in tasks if not has_predecessor(t)]

    order: list[int] = []
    visited: set[int] = set()
    queue = list(roots)
    while queue:
        tid = queue.pop(0)
        if tid in visited:
            continue
        visited.add(tid)
        order.append(tid)
        queue.extend(successors.get(tid, []))
    for t in tasks:  # orphaned by a cycle, if any slipped past db.py's own check
        if t["id"] not in visited:
            order.append(t["id"])

    earliest: dict[int, tuple[date, date]] = {}
    for tid in order:
        t = by_id[tid]
        dur = _duration(t)
        own_start, own_finish = _task_dates(t)
        pred_id = t.get("blocked_by_id")
        pred_times = earliest.get(pred_id) if pred_id is not None else None

        if t.get("pinned") or not pred_times:
            earliest[tid] = (own_start, own_finish)
            continue
        dep_type = t.get("dependency_type") or "FS"
        lag = t.get("lag_days") or 0
        earliest[tid] = _forward_constraint(dep_type, lag, pred_times[0], pred_times[1], dur)

    project_end = max((f for _, f in earliest.values()), default=date.today())

    latest: dict[int, tuple[date, date]] = {}
    for tid in reversed(order):
        t = by_id[tid]
        dur = _duration(t)
        succ_ids = successors.get(tid, [])
        if not succ_ids:
            latest[tid] = (project_end - timedelta(days=dur), project_end)
            continue
        best_finish = None
        for succ_id in succ_ids:
            succ = by_id[succ_id]
            succ_latest = latest[succ_id]
            dep_type = succ.get("dependency_type") or "FS"
            lag = succ.get("lag_days") or 0
            candidate = _backward_constraint(dep_type, lag, succ_latest[0], succ_latest[1], dur)
            if best_finish is None or candidate < best_finish:
                best_finish = candidate
        latest[tid] = (best_finish - timedelta(days=dur), best_finish)

    result = {}
    for t in tasks:
        tid = t["id"]
        e_start, e_finish = earliest[tid]
        l_start, _l_finish = latest[tid]
        slack_days = (l_start - e_start).days
        result[tid] = {
            "start": e_start, "finish": e_finish,
            "latest_start": l_start, "latest_finish": latest[tid][1],
            "slack_days": slack_days, "critical": slack_days <= 0,
            "duration_days": _duration(t),
        }
    return result
