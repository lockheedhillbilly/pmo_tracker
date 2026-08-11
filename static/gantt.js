/* Gantt scheduling engine — pure functions, no DOM. Runs the same in the browser
 * (window.GanttEngine) and under Node (module.exports) so the CPM math can be
 * unit-tested without spinning up the dashboard.
 *
 * Model: each task has at most one predecessor (`blocked_by_id`), so the dependency
 * graph is a forest (tree of chains with fan-out), not a general DAG. That means the
 * forward pass never has to merge multiple predecessors, and the backward pass only
 * ever takes a min over a task's successors.
 */
(function (root) {
  const MS_PER_DAY = 24 * 60 * 60 * 1000;

  function parseISO(s) {
    if (!s) return null;
    const [y, m, d] = s.split("-").map(Number);
    return new Date(Date.UTC(y, m - 1, d));
  }

  function toISO(date) {
    return date.toISOString().slice(0, 10);
  }

  function addDays(date, n) {
    return new Date(date.getTime() + n * MS_PER_DAY);
  }

  function diffDays(a, b) {
    return Math.round((a.getTime() - b.getTime()) / MS_PER_DAY);
  }

  /** Effective [start, end] dates for a task, in Date form. Tasks without an explicit
   * start_date are treated as zero-duration markers sitting on their due date — legacy
   * Board rows that predate the Gantt fields still render as a single-day sliver instead
   * of erroring, and become real bars as soon as someone sets a start date. */
  function taskDates(t) {
    const end = parseISO(t.due);
    const start = t.start_date ? parseISO(t.start_date) : end;
    return { start, end: t.is_milestone ? start : end };
  }

  function duration(t) {
    const { start, end } = taskDates(t);
    if (!start || !end) return 0;
    return Math.max(0, diffDays(end, start));
  }

  function buildGraph(tasks) {
    const byId = new Map(tasks.map((t) => [t.id, t]));
    const successors = new Map(tasks.map((t) => [t.id, []]));
    for (const t of tasks) {
      if (t.blocked_by_id != null && byId.has(t.blocked_by_id)) {
        successors.get(t.blocked_by_id).push(t.id);
      }
    }
    const hasPredecessor = (t) => t.blocked_by_id != null && byId.has(t.blocked_by_id);
    const roots = tasks.filter((t) => !hasPredecessor(t)).map((t) => t.id);
    return { byId, successors, roots };
  }

  /** Predecessor-before-successor order, guarding against cycles (shouldn't exist —
   * db.py rejects them on write — but a corrupt row must never hang the scheduler). */
  function topoOrder(tasks, byId, successors, roots) {
    const order = [];
    const visited = new Set();
    const queue = [...roots];
    while (queue.length) {
      const id = queue.shift();
      if (visited.has(id)) continue;
      visited.add(id);
      order.push(id);
      for (const succId of successors.get(id) || []) queue.push(succId);
    }
    // Any task not reached (orphaned by a cycle) still needs a schedule slot —
    // append it as if it were a root so the caller gets a result for every task.
    for (const t of tasks) {
      if (!visited.has(t.id)) order.push(t.id);
    }
    return order;
  }

  /** Where an edge (dependency_type/lag_days on the *successor*) places the successor
   * relative to the predecessor's own computed [start, finish]. */
  function applyForwardConstraint(depType, lag, predStart, predFinish, succDuration) {
    switch (depType) {
      case "SS":
        { const start = addDays(predStart, lag); return { start, finish: addDays(start, succDuration) }; }
      case "FF":
        { const finish = addDays(predFinish, lag); return { start: addDays(finish, -succDuration), finish }; }
      case "SF":
        { const finish = addDays(predStart, lag); return { start: addDays(finish, -succDuration), finish }; }
      default: // FS
        { const start = addDays(predFinish, lag); return { start, finish: addDays(start, succDuration) }; }
    }
  }

  /** Mirror of applyForwardConstraint for the backward pass: given the successor's
   * *latest* [start, finish], what's the latest the predecessor can finish? */
  function applyBackwardConstraint(depType, lag, succLatestStart, succLatestFinish, predDuration) {
    switch (depType) {
      case "SS":
        { const predLatestStart = addDays(succLatestStart, -lag); return addDays(predLatestStart, predDuration); }
      case "FF":
        return addDays(succLatestFinish, -lag);
      case "SF":
        { const predLatestStart = addDays(succLatestFinish, -lag); return addDays(predLatestStart, predDuration); }
      default: // FS
        return addDays(succLatestStart, -lag);
    }
  }

  /**
   * Forward + backward CPM pass over the whole task list.
   * Returns a Map<id, { start, finish, latestStart, latestFinish, slackDays, critical,
   *                      durationDays, conflict, projected }> — all Date fields as ISO strings.
   */
  function computeSchedule(tasks) {
    const { byId, successors, roots } = buildGraph(tasks);
    const order = topoOrder(tasks, byId, successors, roots);
    const earliest = new Map(); // id -> {start, finish}
    const conflicts = new Set();

    for (const id of order) {
      const t = byId.get(id);
      const dur = duration(t);
      const own = taskDates(t);
      const pred = t.blocked_by_id != null ? byId.get(t.blocked_by_id) : null;
      const predTimes = pred ? earliest.get(pred.id) : null;

      if (t.pinned || !predTimes) {
        // Pinned tasks (and roots / tasks whose predecessor is missing) sit exactly
        // where they're authored; they don't get pushed around by dependencies.
        earliest.set(id, { start: own.start, finish: own.end });
        if (predTimes && t.pinned) {
          const depType = t.dependency_type || "FS";
          const wanted = applyForwardConstraint(depType, t.lag_days || 0, predTimes.start, predTimes.finish, dur);
          if (wanted.start.getTime() > own.start.getTime()) conflicts.add(id);
        }
        continue;
      }
      const depType = t.dependency_type || "FS";
      const computed = applyForwardConstraint(depType, t.lag_days || 0, predTimes.start, predTimes.finish, dur);
      earliest.set(id, computed);
    }

    const projectEnd = Math.max(...[...earliest.values()].map((v) => v.finish.getTime()));

    // Backward pass: process in reverse topo order so every successor is resolved
    // before its predecessor asks it for a "latest start" constraint.
    const latest = new Map();
    for (const id of [...order].reverse()) {
      const t = byId.get(id);
      const dur = duration(t);
      const succIds = successors.get(id) || [];
      if (succIds.length === 0) {
        const latestFinish = new Date(projectEnd);
        latest.set(id, { start: addDays(latestFinish, -dur), finish: latestFinish });
        continue;
      }
      let bestFinish = null;
      for (const succId of succIds) {
        const succ = byId.get(succId);
        const succLatest = latest.get(succId);
        const depType = succ.dependency_type || "FS";
        const candidate = applyBackwardConstraint(depType, succ.lag_days || 0, succLatest.start, succLatest.finish, dur);
        if (bestFinish === null || candidate.getTime() < bestFinish.getTime()) bestFinish = candidate;
      }
      latest.set(id, { start: addDays(bestFinish, -dur), finish: bestFinish });
    }

    const result = new Map();
    for (const t of tasks) {
      const e = earliest.get(t.id);
      const l = latest.get(t.id);
      const slackDays = l && e ? diffDays(l.start, e.start) : 0;
      result.set(t.id, {
        start: toISO(e.start),
        finish: toISO(e.finish),
        latestStart: toISO(l.start),
        latestFinish: toISO(l.finish),
        slackDays,
        critical: slackDays <= 0,
        durationDays: duration(t),
        conflict: conflicts.has(t.id),
      });
    }
    return result;
  }

  /**
   * Move task `id` to a new [start, finish] and cascade the shift onto its non-pinned
   * successors (recursively), honoring each edge's dependency_type/lag_days. Pinned
   * successors are left untouched but reported as conflicts if the new predecessor
   * timing now violates them. Does not mutate `tasks` — returns a diff for the caller
   * (dashboard.js) to preview, then apply via PATCH /api/tasks/<id> per row.
   */
  function rescheduleFrom(tasks, id, newStartISO, newFinishISO) {
    const { byId, successors } = buildGraph(tasks);
    const before = computeSchedule(tasks);
    const oldProjectEnd = maxFinish(before);

    const overrides = new Map(); // id -> {start, finish}
    overrides.set(id, { start: parseISO(newStartISO), finish: parseISO(newFinishISO) });

    const conflicts = [];
    const visited = new Set([id]);
    const queue = [id];
    while (queue.length) {
      const curId = queue.shift();
      const curTimes = overrides.get(curId);
      for (const succId of successors.get(curId) || []) {
        const succ = byId.get(succId);
        const dur = duration(succ);
        const depType = succ.dependency_type || "FS";
        const computed = applyForwardConstraint(depType, succ.lag_days || 0, curTimes.start, curTimes.finish, dur);
        if (succ.pinned) {
          const own = taskDates(succ);
          if (computed.start.getTime() > own.start.getTime()) {
            conflicts.push({ id: succId, reason: "pinned task conflicts with upstream dependency" });
          }
          continue; // pinned successors never move automatically
        }
        if (visited.has(succId)) continue;
        visited.add(succId);
        overrides.set(succId, computed);
        queue.push(succId);
      }
    }

    const updates = [];
    for (const [taskId, times] of overrides) {
      const t = byId.get(taskId);
      const own = taskDates(t);
      const startISO = toISO(times.start);
      const finishISO = toISO(times.finish);
      if (taskId === id || startISO !== toISO(own.start) || finishISO !== toISO(own.end)) {
        updates.push({ id: taskId, start_date: startISO, due: finishISO });
      }
    }

    const patched = tasks.map((t) => {
      const ov = overrides.get(t.id);
      if (!ov) return t;
      return { ...t, start_date: toISO(ov.start), due: toISO(ov.finish) };
    });
    const after = computeSchedule(patched);
    const newProjectEnd = maxFinish(after);

    return {
      updates,
      affectedCount: updates.length - 1 >= 0 ? updates.length - 1 : 0,
      oldProjectEnd: toISO(new Date(oldProjectEnd)),
      newProjectEnd: toISO(new Date(newProjectEnd)),
      conflicts,
    };
  }

  function maxFinish(scheduleMap) {
    let max = -Infinity;
    for (const v of scheduleMap.values()) {
      const t = parseISO(v.finish).getTime();
      if (t > max) max = t;
    }
    return max;
  }

  const api = { computeSchedule, rescheduleFrom, parseISO, toISO, addDays, diffDays, duration, taskDates };

  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  } else {
    root.GanttEngine = api;
  }
})(typeof window !== "undefined" ? window : globalThis);
