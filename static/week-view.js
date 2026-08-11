// ---------- "This Week" tab — a read-through, no-edit lens over the same TASKS array as
// Board/Gantt (categorized risk lists + a Last/This/Next-week strip for meeting walkthroughs).
// Clicking any item jumps to the Gantt tab with that task selected.
let weekSubview = "categories";

function weekBoundaries() {
  const t = GanttEngine.parseISO(today());
  const diffFromMonday = (t.getUTCDay() + 6) % 7;
  const thisMon = GanttEngine.addDays(t, -diffFromMonday);
  return {
    lastMon: GanttEngine.addDays(thisMon, -7), lastSun: GanttEngine.addDays(thisMon, -1),
    thisMon, thisSun: GanttEngine.addDays(thisMon, 6),
    nextMon: GanttEngine.addDays(thisMon, 7), nextSun: GanttEngine.addDays(thisMon, 13),
  };
}
function weekBoundariesISO() {
  const b = weekBoundaries();
  const out = {};
  for (const k in b) out[k] = GanttEngine.toISO(b[k]);
  return out;
}

function weekMetricCard(cls, iconName, val, label) {
  return `<div class="metric ${cls}"><span class="metric-badge">${icon(iconName)}</span><div><b>${val}</b>${esc(label)}</div></div>`;
}

function weekTaskRow(t, schedule) {
  const s = schedule.get(t.id);
  const critical = s && s.critical && t.status === "Open";
  return `<div class="week-item" data-week-goto="${t.id}">
    <span class="week-item-title">${t.is_milestone ? "&#9670; " : ""}${esc(t.task)}</span>
    <span class="week-item-meta">${esc(t.owner || "—")} &middot; due ${esc(t.due)}${critical ? ' &middot; <b style="color:var(--amber);">critical</b>' : ""}</span>
  </div>`;
}

function weekColHtml(title, list, emptyMsg, schedule, countBadge = true) {
  return `<div class="week-col">
    <div class="week-col-head">${esc(title)} ${countBadge ? `<span class="week-col-count">${list.length}</span>` : ""}</div>
    ${list.length ? list.map((t) => weekTaskRow(t, schedule)).join("") : `<div class="week-empty">${esc(emptyMsg)}</div>`}
  </div>`;
}

function renderWeekView() {
  const pane = document.getElementById("week-view");
  if (!pane) return;
  if (!TASKS.length) {
    pane.innerHTML = `<div class="gantt-empty">No tasks yet — add one from the Board tab.</div>`;
    return;
  }

  const schedule = GanttEngine.computeSchedule(TASKS);
  const iso = weekBoundariesISO();
  const t0 = today();

  const overdue = TASKS.filter((t) => t.status === "Open" && t.due < t0);
  const dueThisWeek = TASKS.filter((t) => t.status === "Open" && t.due >= iso.thisMon && t.due <= iso.thisSun);
  const startingNextWeek = TASKS.filter((t) => { const s = schedule.get(t.id); return s && s.start >= iso.nextMon && s.start <= iso.nextSun; });
  const criticalOpen = TASKS.filter((t) => t.status === "Open" && schedule.get(t.id)?.critical);
  const upcomingMilestones = TASKS.filter((t) => t.is_milestone && t.due >= t0).sort((a, b) => a.due.localeCompare(b.due)).slice(0, 6);
  const completedThisWeek = TASKS.filter((t) => t.status === "Done" && t.updated_at.slice(0, 10) >= iso.thisMon && t.updated_at.slice(0, 10) <= iso.thisSun);
  const blocked = TASKS.filter((t) => t.status === "Open" && t.execution_state === "Blocked");
  const dueThisWeekIds = new Set(dueThisWeek.map((t) => t.id));
  const overdueIds = new Set(overdue.map((t) => t.id));
  const criticalIds = new Set(criticalOpen.map((t) => t.id));
  // "At risk" is a lightweight heuristic, not the full project-level risk engine from the spec —
  // overdue, or due this week with no progress yet, or due this week and on the critical path.
  const atRisk = TASKS.filter((t) => t.status === "Open" && t.execution_state !== "Blocked" &&
    (overdueIds.has(t.id) || (dueThisWeekIds.has(t.id) && (t.execution_state === "Not started" || criticalIds.has(t.id)))));

  pane.innerHTML = `
    <div class="summary">
      ${weekMetricCard("m-dueWeek", "calendar", dueThisWeek.length, "Due this week")}
      ${weekMetricCard("m-completed", "check", completedThisWeek.length, "Completed this week")}
      ${weekMetricCard("m-myReviews", "alert", atRisk.length, "At risk")}
      ${weekMetricCard("m-overdue", "alert", blocked.length, "Blocked")}
    </div>
    <div class="week-subtabs">
      <button class="secondary small${weekSubview === "categories" ? " toggle-on" : ""}" data-weeksub="categories">Categories</button>
      <button class="secondary small${weekSubview === "byday" ? " toggle-on" : ""}" data-weeksub="byday">By day</button>
      <button class="secondary small${weekSubview === "threeweek" ? " toggle-on" : ""}" data-weeksub="threeweek">Last / This / Next week</button>
    </div>
    ${weekSubview === "categories"
      ? `<div class="week-grid">
          ${weekColHtml("Overdue", overdue, "Nothing overdue", schedule)}
          ${weekColHtml("Due this week", dueThisWeek, "Nothing due this week", schedule)}
          ${weekColHtml("Starting next week", startingNextWeek, "Nothing scheduled to start", schedule)}
          ${weekColHtml("Critical path", criticalOpen, "No critical tasks open", schedule)}
          ${weekColHtml("Upcoming milestones", upcomingMilestones, "No upcoming milestones", schedule)}
        </div>`
      : weekSubview === "byday"
      ? weekByDayHtml(iso, schedule)
      : weekThreeColumnHtml(iso, schedule)}
  `;
  wireWeekEvents();
}

// Day-by-day breakdown of the current week (Mon–Fri always shown; Sat/Sun only if something's
// actually due then) — the "day view along the date" a live meeting walkthrough needs, as
// distinct from the category buckets above or the 3-week due-range comparison.
function weekByDayHtml(iso, schedule) {
  const dayNames = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];
  const t0 = today();
  const mon = GanttEngine.parseISO(iso.thisMon);
  const blocks = [];
  for (let i = 0; i < 7; i++) {
    const d = GanttEngine.toISO(GanttEngine.addDays(mon, i));
    const list = TASKS.filter((t) => t.due === d).sort((a, b) => (a.priority === "High" ? 0 : 1) - (b.priority === "High" ? 0 : 1));
    if (i >= 5 && !list.length) continue; // hide empty weekend days
    const label = `${dayNames[i]} &middot; ${fmtDateShort(d)}${d === t0 ? " — Today" : ""}`;
    blocks.push(weekColHtml(label, list, "Nothing due", schedule));
  }
  return `<div class="week-day-list">${blocks.join("")}</div>`;
}

function weekThreeColumnHtml(iso, schedule) {
  const inRange = (due, mon, sun) => due >= mon && due <= sun;
  const last = TASKS.filter((t) => inRange(t.due, iso.lastMon, iso.lastSun));
  const thisW = TASKS.filter((t) => inRange(t.due, iso.thisMon, iso.thisSun));
  const next = TASKS.filter((t) => inRange(t.due, iso.nextMon, iso.nextSun));
  const col = (title, range, list) => `<div class="week-col">
    <div class="week-col-head">${esc(title)} <span class="added-text">${esc(range)}</span></div>
    ${list.length ? list.map((t) => weekTaskRow(t, schedule)).join("") : `<div class="week-empty">No tasks due</div>`}
  </div>`;
  return `<div class="week-grid three">
    ${col("Last week", `${iso.lastMon} – ${iso.lastSun}`, last)}
    ${col("This week", `${iso.thisMon} – ${iso.thisSun}`, thisW)}
    ${col("Next week", `${iso.nextMon} – ${iso.nextSun}`, next)}
  </div>`;
}

function wireWeekEvents() {
  document.querySelectorAll("[data-weeksub]").forEach((b) => {
    b.onclick = () => { weekSubview = b.dataset.weeksub; renderWeekView(); };
  });
  document.querySelectorAll("[data-week-goto]").forEach((el) => {
    el.onclick = () => {
      ganttSelectedId = parseInt(el.dataset.weekGoto);
      document.querySelector('.tab[data-tab="gantt"]').click();
    };
  });
}
