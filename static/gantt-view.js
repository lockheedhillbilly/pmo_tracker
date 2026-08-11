// ---------- Gantt tab — DOM rendering over GanttEngine (static/gantt.js) + the same
// TASKS array the Board uses. Left pane is an editable hierarchy table; right pane is an
// SVG timeline. Both panes' row scroll positions are kept in sync manually since they're
// two separate scroll containers (the timeline also needs independent horizontal scroll).
const GANTT_ROW_H = 32;
const GANTT_ZOOM_PX = { day: 36, week: 13, month: 4 };
const GANTT_STATUS_LABELS = ["Not started", "In progress", "Blocked", "Complete"];

let ganttZoom = localStorage.getItem("pmo_gantt_zoom") || "week";
let ganttCollapsed = new Set(JSON.parse(localStorage.getItem("pmo_gantt_collapsed") || "[]"));
let ganttShowCritical = false;
let ganttSelectedId = null;
let ganttMultiSelected = new Set();
let ganttUndo = null; // single-slot undo — set by delete/duplicate, consumed by Ctrl/Cmd+Z
let ganttDragId = null;

function saveGanttCollapsed() { localStorage.setItem("pmo_gantt_collapsed", JSON.stringify([...ganttCollapsed])); }

function ganttStatusLabel(t) {
  if (t.status === "Done") return "Complete";
  return t.execution_state || "Not started";
}
function ganttStatusClass(label) {
  return "status-" + label.toLowerCase().replace(/\s+/g, "-");
}
async function ganttSetStatus(id, label) {
  if (label === "Complete") await patchTask(id, { status: "Done" });
  else await patchTask(id, { status: "Open", execution_state: label });
  renderGanttView();
}

// ---------- Tree helpers ----------
function ganttBuildChildren(tasks) {
  const byParent = new Map();
  tasks.forEach((t) => {
    const p = t.parent_id || null;
    if (!byParent.has(p)) byParent.set(p, []);
    byParent.get(p).push(t);
  });
  for (const list of byParent.values()) list.sort((a, b) => a.sort_order - b.sort_order);
  return byParent;
}
function ganttFlattenVisible(byParent) {
  const rows = [];
  (function walk(parentId, depth) {
    for (const t of byParent.get(parentId) || []) {
      const kids = byParent.get(t.id) || [];
      rows.push({ task: t, depth, hasKids: kids.length > 0 });
      if (kids.length && !ganttCollapsed.has(t.id)) walk(t.id, depth + 1);
    }
  })(null, 0);
  return rows;
}
function ganttPredecessorsAndSuccessors(id, tasks) {
  const byId = new Map(tasks.map((t) => [t.id, t]));
  const preds = new Set();
  let cur = byId.get(id);
  let depth = 0;
  while (cur && cur.blocked_by_id != null && depth < 1000) {
    preds.add(cur.blocked_by_id);
    cur = byId.get(cur.blocked_by_id);
    depth++;
  }
  const succs = new Set();
  const queue = [id];
  while (queue.length) {
    const cid = queue.shift();
    for (const t of tasks) {
      if (t.blocked_by_id === cid && !succs.has(t.id)) { succs.add(t.id); queue.push(t.id); }
    }
  }
  return { preds, succs };
}

// ---------- Timeline math ----------
function ganttDateRange(tasks, schedule) {
  let minD = null, maxD = null;
  tasks.forEach((t) => {
    const s = schedule.get(t.id);
    if (!s) return;
    const sd = GanttEngine.parseISO(s.start), fd = GanttEngine.parseISO(s.finish);
    if (!minD || sd < minD) minD = sd;
    if (!maxD || fd > maxD) maxD = fd;
  });
  const todayD = GanttEngine.parseISO(today());
  if (!minD) minD = todayD;
  if (!maxD) maxD = todayD;
  if (todayD < minD) minD = todayD;
  if (todayD > maxD) maxD = todayD;
  minD = GanttEngine.addDays(minD, -3);
  maxD = GanttEngine.addDays(maxD, 7);
  return { minD, maxD };
}
function ganttTicks(minD, maxD, zoom) {
  const totalDays = GanttEngine.diffDays(maxD, minD);
  const ticks = [];
  if (zoom === "day") {
    for (let i = 0; i <= totalDays; i++) ticks.push(GanttEngine.addDays(minD, i));
  } else if (zoom === "month") {
    let d = new Date(Date.UTC(minD.getUTCFullYear(), minD.getUTCMonth(), 1));
    while (d <= maxD) { ticks.push(new Date(d)); d = new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth() + 1, 1)); }
  } else {
    let d = new Date(minD);
    while (d.getUTCDay() !== 1) d = GanttEngine.addDays(d, 1); // roll forward to Monday
    while (d <= maxD) { ticks.push(new Date(d)); d = GanttEngine.addDays(d, 7); }
  }
  return ticks;
}
function ganttTickLabel(d, zoom) {
  if (zoom === "month") return d.toLocaleDateString("en-GB", { month: "long", year: "numeric" });
  return d.toLocaleDateString("en-GB", { day: "numeric", month: "short" });
}

// ---------- Main render ----------
function renderGanttView() {
  const pane = document.getElementById("gantt-view");
  if (!pane) return;

  if (!TASKS.length) {
    pane.innerHTML = `<div class="gantt-empty">No tasks yet — add one from the Board tab, or use "+ Add task" here once you have at least one.</div>`;
    return;
  }

  const schedule = GanttEngine.computeSchedule(TASKS);
  const byParent = ganttBuildChildren(TASKS);
  const rows = ganttFlattenVisible(byParent);
  const { minD, maxD } = ganttDateRange(TASKS, schedule);
  const pxPerDay = GANTT_ZOOM_PX[ganttZoom];
  const totalDays = GanttEngine.diffDays(maxD, minD);
  const svgWidth = Math.max(totalDays * pxPerDay, 400);
  const bodyHeight = Math.max(rows.length * GANTT_ROW_H, GANTT_ROW_H);
  const todayX = GanttEngine.diffDays(GanttEngine.parseISO(today()), minD) * pxPerDay;

  let related = { preds: new Set(), succs: new Set() };
  if (ganttSelectedId != null) related = ganttPredecessorsAndSuccessors(ganttSelectedId, TASKS);

  // Preserve scroll position across re-renders triggered by edits/toggles.
  const prevY = document.getElementById("gantt-table-scroll-y")?.scrollTop || 0;
  const prevX = document.getElementById("gantt-timeline-scroll-x")?.scrollLeft || 0;

  pane.innerHTML = `
    <div class="gantt-toolbar">
      <div class="left">
        <button class="secondary small" id="gantt-add-top">+ Add task</button>
        <button class="secondary small" id="gantt-expand-all">Expand all</button>
        <button class="secondary small" id="gantt-collapse-all">Collapse all</button>
      </div>
      <div class="right">
        <div class="gantt-zoom">
          <button data-zoom="day" class="${ganttZoom === "day" ? "active" : ""}">Day</button>
          <button data-zoom="week" class="${ganttZoom === "week" ? "active" : ""}">Week</button>
          <button data-zoom="month" class="${ganttZoom === "month" ? "active" : ""}">Month</button>
        </div>
        <button class="secondary small${ganttShowCritical ? " toggle-on" : ""}" id="gantt-critical-toggle">Show critical path</button>
        <button class="secondary small" id="gantt-import-btn">Import Excel</button>
        <input type="file" id="gantt-import-file" accept=".xlsx,.csv" style="display:none;">
        <button class="secondary small" id="gantt-export-btn">Export Excel</button>
      </div>
    </div>
    ${ganttMultiSelected.size ? ganttBulkbarHtml() : ""}
    <div class="gantt-body">
      <div class="gantt-table-pane">
        ${ganttTableHeaderHtml()}
        <div class="gantt-table-scroll-y" id="gantt-table-scroll-y">
          ${rows.map((r) => ganttTableRowHtml(r, schedule, related)).join("")}
        </div>
      </div>
      <div class="gantt-timeline-pane">
        <div class="gantt-timeline-scroll-x" id="gantt-timeline-scroll-x">
          <div class="gantt-timeline-header" style="width:${svgWidth}px;">
            ${ganttHeaderSvg(minD, maxD, pxPerDay, svgWidth, todayX)}
          </div>
          <div class="gantt-timeline-scroll-y" id="gantt-timeline-scroll-y">
            ${ganttBodySvg(rows, schedule, minD, pxPerDay, svgWidth, bodyHeight, todayX, related)}
          </div>
        </div>
      </div>
    </div>
  `;

  document.getElementById("gantt-table-scroll-y").scrollTop = prevY;
  document.getElementById("gantt-timeline-scroll-y").scrollTop = prevY;
  document.getElementById("gantt-timeline-scroll-x").scrollLeft = prevX;
  wireGanttEvents(rows, schedule, pxPerDay);
}

function ganttBulkbarHtml() {
  return `<div class="gantt-bulkbar">
    <span>${ganttMultiSelected.size} selected</span>
    <label>Owner <input id="gantt-bulk-owner" style="width:100px;"></label>
    <button class="small" id="gantt-bulk-owner-apply">Apply</button>
    <label>Status
      <select id="gantt-bulk-status">${GANTT_STATUS_LABELS.map((l) => `<option>${esc(l)}</option>`).join("")}</select>
    </label>
    <button class="small" id="gantt-bulk-status-apply">Apply</button>
    <button class="secondary small" id="gantt-bulk-clear">Clear selection</button>
  </div>`;
}

function ganttTableHeaderHtml() {
  return `<div class="gantt-table-header gantt-row-grid">
    <div class="gantt-cell">Task</div>
    <div class="gantt-cell">Owner</div>
    <div class="gantt-cell">Status</div>
    <div class="gantt-cell">Start</div>
    <div class="gantt-cell">End</div>
    <div class="gantt-cell">Dur.</div>
    <div class="gantt-cell">Depends on</div>
    <div class="gantt-cell"></div>
  </div>`;
}

function ganttTableRowHtml(row, schedule, related) {
  const t = row.task;
  const s = schedule.get(t.id) || {};
  const statusLabel = ganttStatusLabel(t);
  const selected = t.id === ganttSelectedId || ganttMultiSelected.has(t.id);
  const relatedCls = related.preds.has(t.id) || related.succs.has(t.id) ? "dep-highlight" : "";
  const dep = t.blocked_by_id != null ? taskById(t.blocked_by_id) : null;
  const statusOpts = GANTT_STATUS_LABELS.map((l) => `<option value="${esc(l)}"${l === statusLabel ? " selected" : ""}>${esc(l)}</option>`).join("");
  return `<div class="gantt-row gantt-row-grid${selected ? " selected" : ""}${relatedCls ? " " + relatedCls : ""}" data-gantt-row="${t.id}" draggable="true">
    <div class="gantt-cell gantt-task-cell" style="padding-left:${8 + row.depth * 16}px;">
      <span class="gantt-caret${row.hasKids ? "" : " leaf"}" data-caret="${t.id}">${row.hasKids ? (ganttCollapsed.has(t.id) ? "&#9656;" : "&#9662;") : ""}</span>
      ${t.is_milestone ? `<span class="gantt-milestone-flag" title="Milestone">&#9670;</span>` : ""}
      <span class="gantt-cell-text editable" data-edit="task" data-id="${t.id}" title="${esc(t.task)}">${esc(t.task)}</span>
      <button class="gantt-add-child-btn" data-add-child="${t.id}" title="Add subtask">+</button>
      <button class="gantt-rowmenu-btn" data-gantt-menu="${t.id}" title="More">&#8942;</button>
    </div>
    <div class="gantt-cell editable" data-edit="owner" data-id="${t.id}" title="${esc(t.owner || "")}">${esc(t.owner || "—")}</div>
    <div class="gantt-cell">
      <select data-status="${t.id}">${statusOpts}</select>
    </div>
    <div class="gantt-cell editable" data-edit="start_date" data-id="${t.id}">${esc(s.start || "—")}</div>
    <div class="gantt-cell editable" data-edit="due" data-id="${t.id}">${esc(s.finish || "—")}</div>
    <div class="gantt-cell">${s.durationDays != null ? s.durationDays + "d" : "—"}</div>
    <div class="gantt-cell editable" data-edit="blocked_by_id" data-id="${t.id}" title="${dep ? esc(dep.task) : ""}">${dep ? "#" + dep.id + " " + esc(dep.task.slice(0, 18)) : "—"}</div>
    <div class="gantt-cell"></div>
  </div>`;
}

function ganttHeaderSvg(minD, maxD, pxPerDay, width, todayX) {
  const ticks = ganttTicks(minD, maxD, ganttZoom);
  let inner = ticks.map((d) => {
    const x = GanttEngine.diffDays(d, minD) * pxPerDay;
    return `<line x1="${x}" y1="0" x2="${x}" y2="32" class="gantt-grid-line"/><text x="${x + 4}" y="19" class="gantt-grid-label">${esc(ganttTickLabel(d, ganttZoom))}</text>`;
  }).join("");
  if (todayX >= 0 && todayX <= width) {
    inner += `<line x1="${todayX}" y1="0" x2="${todayX}" y2="32" class="gantt-today-line"/><text x="${todayX + 4}" y="30" class="gantt-today-label">Today</text>`;
  }
  return `<svg width="${width}" height="32">${inner}</svg>`;
}

function ganttBodySvg(rows, schedule, minD, pxPerDay, width, height, todayX, related) {
  const rowIndex = new Map(rows.map((r, i) => [r.task.id, i]));
  let bands = "", bars = "", deps = "";

  rows.forEach((r, i) => {
    const y = i * GANTT_ROW_H;
    bands += `<rect class="gantt-row-band" x="0" y="${y}" width="${width}" height="${GANTT_ROW_H}"/>`;
  });

  rows.forEach((r, i) => {
    const t = r.task;
    const s = schedule.get(t.id);
    if (!s) return;
    const y = i * GANTT_ROW_H;
    const startX = GanttEngine.diffDays(GanttEngine.parseISO(s.start), minD) * pxPerDay;
    const endX = GanttEngine.diffDays(GanttEngine.parseISO(s.finish), minD) * pxPerDay;
    const selected = t.id === ganttSelectedId;
    const dimmed = ganttShowCritical && !s.critical;
    const statusCls = ganttStatusClass(ganttStatusLabel(t));
    const cls = ["gantt-bar", statusCls];
    if (dimmed) cls.push("dimmed");
    if (ganttShowCritical && s.critical) cls.push("critical");
    if (selected) cls.push("selected");
    const titleText = `${t.task}\nSlack: ${s.slackDays}d — ${s.critical ? "Critical" : "Not critical"}${s.conflict ? "\nConflict: pinned date clashes with dependency" : ""}`;

    if (t.is_milestone) {
      const cx = startX, cy = y + GANTT_ROW_H / 2, sz = 8;
      const mCls = ["gantt-milestone-shape"];
      if (dimmed) mCls.push("dimmed");
      if (selected) mCls.push("selected");
      bars += `<g data-bar="${t.id}"><title>${esc(titleText)}</title><polygon class="${mCls.join(" ")}" points="${cx},${cy - sz} ${cx + sz},${cy} ${cx},${cy + sz} ${cx - sz},${cy}"/></g>`;
    } else {
      const barY = y + 6, barH = GANTT_ROW_H - 12;
      const w = Math.max(endX - startX, 3);
      const pct = Math.max(0, Math.min(100, t.percent_complete || 0));
      bars += `<g data-bar="${t.id}"><title>${esc(titleText)}</title>
        <rect class="${cls.join(" ")}" x="${startX}" y="${barY}" width="${w}" height="${barH}" rx="6"/>
        ${pct > 0 ? `<rect class="gantt-bar-progress" x="${startX}" y="${barY}" width="${(w * pct) / 100}" height="${barH}" rx="6"/>` : ""}
        <text class="gantt-bar-label" x="${endX + 6}" y="${barY + barH / 2 + 4}">${esc(t.task.slice(0, 40))}</text>
        <rect class="gantt-resize-handle" data-resize="${t.id}" x="${startX + w - 5}" y="${barY}" width="8" height="${barH}" fill="transparent"/>
      </g>`;
    }
  });

  rows.forEach((r) => {
    const t = r.task;
    if (t.blocked_by_id == null || !rowIndex.has(t.blocked_by_id)) return;
    const predIdx = rowIndex.get(t.blocked_by_id);
    const succIdx = rowIndex.get(t.id);
    const pred = taskById(t.blocked_by_id);
    const predS = schedule.get(pred.id), succS = schedule.get(t.id);
    if (!predS || !succS) return;
    const depType = t.dependency_type || "FS";
    const predStartX = GanttEngine.diffDays(GanttEngine.parseISO(predS.start), minD) * pxPerDay;
    const predEndX = GanttEngine.diffDays(GanttEngine.parseISO(predS.finish), minD) * pxPerDay;
    const succStartX = GanttEngine.diffDays(GanttEngine.parseISO(succS.start), minD) * pxPerDay;
    const succEndX = GanttEngine.diffDays(GanttEngine.parseISO(succS.finish), minD) * pxPerDay;
    const fromX = (depType === "SS" || depType === "SF") ? predStartX : predEndX;
    const toX = (depType === "FF" || depType === "SF") ? succEndX : succStartX;
    const fromY = predIdx * GANTT_ROW_H + GANTT_ROW_H / 2;
    const toY = succIdx * GANTT_ROW_H + GANTT_ROW_H / 2;
    const midX = fromX + (toX >= fromX ? 10 : -10);
    const relCls = (related.preds.has(t.id) || related.succs.has(t.id) || t.id === ganttSelectedId || pred.id === ganttSelectedId) ? " related" : "";
    deps += `<path class="gantt-dep-line${relCls}" d="M${fromX},${fromY} L${midX},${fromY} L${midX},${toY} L${toX},${toY}" marker-end="url(#gantt-arrow)"/>`;
  });

  const defs = `<defs><marker id="gantt-arrow" markerWidth="8" markerHeight="8" refX="6" refY="3" orient="auto"><path d="M0,0 L6,3 L0,6 Z" fill="var(--mute)"/></marker></defs>`;
  const todayLine = todayX >= 0 && todayX <= width ? `<line x1="${todayX}" y1="0" x2="${todayX}" y2="${height}" class="gantt-today-line"/>` : "";
  return `<svg width="${width}" height="${height}">${defs}${bands}${todayLine}${deps}${bars}</svg>`;
}

// ---------- Reschedule preview (drag-move / drag-resize funnel through here) ----------
function ganttShowRescheduleConfirm(diff, draggedId) {
  const t = taskById(draggedId);
  const endChanged = diff.oldProjectEnd !== diff.newProjectEnd;
  const overlay = document.createElement("div");
  overlay.className = "gantt-modal-overlay";
  overlay.innerHTML = `<div class="gantt-modal">
    <h3>Reschedule &ldquo;${esc(t.task.slice(0, 50))}&rdquo;?</h3>
    <p>This affects ${diff.affectedCount} downstream task${diff.affectedCount === 1 ? "" : "s"}${endChanged ? ` and moves the project end date from ${esc(diff.oldProjectEnd)} to ${esc(diff.newProjectEnd)}` : ""}.</p>
    ${diff.conflicts.length ? `<div class="conflict-note">${diff.conflicts.length} pinned task(s) conflict with this change and will be left where they are.</div>` : ""}
    <div class="affected-list" id="gantt-affected-list" style="display:none;">
      ${diff.updates.map((u) => `<div>#${u.id} ${esc((taskById(u.id) || { task: "?" }).task.slice(0, 40))} → ${esc(u.start_date)} – ${esc(u.due)}</div>`).join("")}
    </div>
    <div class="actions">
      <button class="secondary small" data-act="cancel">Cancel</button>
      <button class="secondary small" data-act="review">Review affected tasks</button>
      <button class="small" data-act="apply">Apply</button>
    </div>
  </div>`;
  document.body.appendChild(overlay);
  overlay.querySelector('[data-act="cancel"]').onclick = () => { overlay.remove(); renderGanttView(); };
  overlay.querySelector('[data-act="review"]').onclick = () => {
    const list = overlay.querySelector("#gantt-affected-list");
    list.style.display = list.style.display === "none" ? "block" : "none";
  };
  overlay.querySelector('[data-act="apply"]').onclick = async () => {
    overlay.remove();
    let failed = 0;
    for (const u of diff.updates) { if (!(await patchTask(u.id, { start_date: u.start_date, due: u.due }))) failed++; }
    await loadTasks();
    renderGanttView();
    const n = diff.updates.length - failed;
    if (n > 0) showToast(`Rescheduled ${n} task${n === 1 ? "" : "s"}${failed ? ` (${failed} failed)` : ""}`);
  };
}

// ---------- Excel import: preview + column-mapping modal, then commit ----------
const GANTT_IMPORT_FIELDS = [
  ["task", "Task", true], ["module", "Workstream"], ["owner", "Owner"], ["status", "Status"],
  ["start_date", "Start"], ["due", "Due"], ["blocked_by", "Depends On"], ["is_milestone", "Milestone"],
];

function ganttShowImportModal(data) {
  const { columns, rows, suggested_mapping } = data;
  if (!columns.length) { showToast("That file has no rows"); return; }
  const overlay = document.createElement("div");
  overlay.className = "gantt-modal-overlay";
  const colOptions = (selectedIdx) => `<option value="">— skip —</option>` +
    columns.map((c, i) => `<option value="${i}"${i === selectedIdx ? " selected" : ""}>${esc(c || `Column ${i + 1}`)}</option>`).join("");
  overlay.innerHTML = `<div class="gantt-modal" style="width:560px;">
    <h3>Import from spreadsheet</h3>
    <p>${rows.length} row${rows.length === 1 ? "" : "s"} found. Map each field to a column, or leave it as "skip" to ignore.</p>
    <div class="affected-list" style="display:block; max-height:220px;">
      ${GANTT_IMPORT_FIELDS.map(([field, label]) => `
        <div style="display:flex; align-items:center; gap:8px; padding:4px 0;">
          <label style="width:110px; flex-shrink:0;">${esc(label)}</label>
          <select data-map-field="${field}" style="flex:1;">${colOptions(suggested_mapping[field])}</select>
        </div>`).join("")}
    </div>
    <div class="affected-list" style="margin-top:8px;">
      ${rows.slice(0, 5).map((r) => `<div>${r.map((c) => esc(String(c).slice(0, 20))).join(" | ")}</div>`).join("")}
    </div>
    <div class="actions">
      <button class="secondary small" data-act="cancel">Cancel</button>
      <button class="small" data-act="import">Import ${rows.length} task${rows.length === 1 ? "" : "s"}</button>
    </div>
  </div>`;
  document.body.appendChild(overlay);
  overlay.querySelector('[data-act="cancel"]').onclick = () => overlay.remove();
  overlay.querySelector('[data-act="import"]').onclick = async () => {
    const mapping = {};
    overlay.querySelectorAll("[data-map-field]").forEach((sel) => {
      if (sel.value !== "") mapping[sel.dataset.mapField] = parseInt(sel.value);
    });
    if (mapping.task === undefined) { showToast("Task column is required"); return; }
    overlay.querySelector('[data-act="import"]').textContent = "Importing…";
    const r = await fetch("/api/gantt/import/commit", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ rows, mapping }) });
    const result = await r.json();
    overlay.remove();
    await loadTasks();
    renderGanttView();
    showToast(`Imported ${result.created} task${result.created === 1 ? "" : "s"}${result.errors?.length ? ` (${result.errors.length} error${result.errors.length === 1 ? "" : "s"})` : ""}`);
  };
}

// ---------- Editing + interactions ----------
function ganttStartEdit(cell, id, field) {
  const t = taskById(id);
  const current = field === "blocked_by_id" ? (t.blocked_by_id || "") : (t[field] || "");
  let input;
  if (field === "blocked_by_id") {
    const others = TASKS.filter((x) => x.id !== id);
    input = document.createElement("select");
    input.innerHTML = `<option value="">— none —</option>` + others.map((x) =>
      `<option value="${x.id}"${x.id === t.blocked_by_id ? " selected" : ""}>#${x.id} ${esc(x.task.slice(0, 40))}</option>`
    ).join("");
  } else if (field === "start_date" || field === "due") {
    input = document.createElement("input");
    input.type = "date";
    input.value = current;
  } else {
    input = document.createElement("input");
    input.type = "text";
    input.value = current;
  }
  cell.innerHTML = "";
  cell.appendChild(input);
  input.focus();
  if (input.select) input.select();

  let committed = false;
  const commit = async () => {
    if (committed) return;
    committed = true;
    const val = input.value;
    if (field === "blocked_by_id") {
      if (val) await patchTask(id, { blocked_by_id: parseInt(val) });
      else await patchTask(id, { clear_blocked_by: true });
    } else {
      await patchTask(id, { [field]: val });
    }
    renderGanttView();
  };
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter") commit();
    if (e.key === "Escape") { committed = true; renderGanttView(); }
  });
  input.addEventListener("blur", commit);
  if (field === "blocked_by_id") input.addEventListener("change", commit);
}

async function ganttAddTask(parentId) {
  const parent = parentId ? taskById(parentId) : null;
  const body = {
    task: "New task", owner: CURRENT_USER, track: parent ? parent.track : "Discovery",
    module: parent ? parent.module : "", start_date: today(), due: tomorrow(),
  };
  if (parentId) body.parent_id = parentId;
  const r = await fetch("/api/tasks", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const created = await r.json();
  if (parentId) ganttCollapsed.delete(parentId);
  await loadTasks();
  ganttSelectedId = created.id;
  renderGanttView();
  const cell = document.querySelector(`.gantt-cell-text[data-id="${created.id}"]`);
  if (cell) ganttStartEdit(cell, created.id, "task");
}

async function ganttDuplicateTask(id) {
  const t = taskById(id);
  const r = await fetch("/api/tasks", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({
      task: t.task, owner: t.owner, module: t.module, track: t.track, priority: t.priority,
      parent_id: t.parent_id, start_date: t.start_date, due: t.due, execution_state: t.execution_state,
    }),
  });
  const created = await r.json();
  await loadTasks();
  ganttSelectedId = created.id;
  ganttUndo = async () => { await fetch(`/api/tasks/${created.id}`, { method: "DELETE" }); await loadTasks(); renderGanttView(); };
  renderGanttView();
  showToast("Duplicated", ganttUndo);
}

function ganttDeleteTask(id) {
  // Reuses the row menu's own confirm-then-delete flow (see ganttOpenRowMenu) rather than
  // deleting outright — Delete on a Gantt task is destructive enough to warrant the same
  // one-extra-click confirmation the "..." menu already gives it.
  const btn = document.querySelector(`[data-gantt-menu="${id}"]`);
  if (!btn) return;
  ganttOpenRowMenu(btn, id);
  document.querySelector('.popover [data-act="delete"]')?.click();
}

async function ganttPromoteOrIndent(id, dir) {
  const rows = ganttFlattenVisible(ganttBuildChildren(TASKS));
  const idx = rows.findIndex((r) => r.task.id === id);
  if (idx < 0) return;
  const t = rows[idx].task;
  if (dir === "indent") {
    if (idx === 0) return;
    const prev = rows[idx - 1].task;
    const ok = await patchTask(id, { parent_id: prev.id });
    if (!ok) return;
    ganttCollapsed.delete(prev.id);
  } else {
    if (t.parent_id == null) return;
    const parent = taskById(t.parent_id);
    const ok = await patchTask(id, parent.parent_id != null ? { parent_id: parent.parent_id } : { clear_parent: true });
    if (!ok) return;
  }
  await loadTasks();
  renderGanttView();
}

async function ganttHandleRowDrop(dragId, targetId, mode) {
  if (dragId == null || dragId === targetId) return;
  const target = taskById(targetId);
  if (mode === "child") {
    const ok = await patchTask(dragId, { parent_id: targetId });
    if (!ok) return;
    const kids = TASKS.filter((t) => t.parent_id === targetId && t.id !== dragId).map((t) => t.id);
    kids.push(dragId);
    await fetch("/api/reorder", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ids: kids }) });
    ganttCollapsed.delete(targetId);
  } else {
    const newParent = target.parent_id || null;
    const ok = await patchTask(dragId, newParent != null ? { parent_id: newParent } : { clear_parent: true });
    if (!ok) return;
    const siblings = TASKS.filter((t) => (t.parent_id || null) === newParent && t.id !== dragId).map((t) => t.id);
    const idx = siblings.indexOf(targetId);
    siblings.splice(mode === "before" ? idx : idx + 1, 0, dragId);
    await fetch("/api/reorder", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ids: siblings }) });
  }
  await loadTasks();
  renderGanttView();
}

function ganttOpenRowMenu(anchor, id) {
  closePopovers();
  const t = taskById(id);
  const pop = document.createElement("div");
  pop.className = "popover";
  pop.innerHTML = `
    <div class="pop-item" data-act="addchild">Add subtask</div>
    <div class="pop-item" data-act="dup">Duplicate</div>
    <div class="pop-item" data-act="milestone">${t.is_milestone ? "Unmark milestone" : "Mark as milestone"}</div>
    <div class="pop-item" data-act="pin">${t.pinned ? "Unpin (auto-schedule)" : "Pin (fix dates)"}</div>
    <hr>
    <div class="pop-item danger" data-act="delete">Delete</div>
  `;
  document.body.appendChild(pop);
  const r = anchor.getBoundingClientRect();
  pop.style.top = (r.bottom + window.scrollY) + "px";
  pop.style.left = (r.left - 160 + window.scrollX) + "px";
  pop.querySelector('[data-act="addchild"]').onclick = () => { closePopovers(); ganttAddTask(id); };
  pop.querySelector('[data-act="dup"]').onclick = () => { closePopovers(); ganttDuplicateTask(id); };
  pop.querySelector('[data-act="milestone"]').onclick = async () => { closePopovers(); await patchTask(id, { is_milestone: !t.is_milestone }); renderGanttView(); };
  pop.querySelector('[data-act="pin"]').onclick = async () => { closePopovers(); await patchTask(id, { pinned: !t.pinned }); renderGanttView(); };
  pop.querySelector('[data-act="delete"]').onclick = (e) => {
    e.stopPropagation();
    pop.innerHTML = `<div style="font-size:12px;margin-bottom:8px;">Delete &ldquo;${esc(t.task.slice(0, 60))}${t.task.length > 60 ? "…" : ""}&rdquo;?</div>
      <div class="actions"><button class="secondary small" data-act="cancel">Cancel</button><button class="small" style="background:var(--red);" data-act="go">Delete</button></div>`;
    pop.querySelector('[data-act="cancel"]').onclick = closePopovers;
    pop.querySelector('[data-act="go"]').onclick = async () => {
      closePopovers();
      await fetch(`/api/tasks/${id}`, { method: "DELETE" });
      await loadTasks();
      ganttUndo = async () => {
        const r = await fetch("/api/tasks", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({
          task: t.task, owner: t.owner, module: t.module, track: t.track, due: t.due, start_date: t.start_date, priority: t.priority,
        }) });
        await r.json(); await loadTasks(); renderGanttView();
      };
      renderGanttView();
      showToast("Deleted", ganttUndo);
    };
  };
}

// ---------- Keyboard shortcuts (Enter/Tab/Shift+Tab/Delete/Ctrl+D/Ctrl+Z) ----------
document.addEventListener("keydown", (e) => {
  const ganttTab = document.querySelector('.tab[data-tab="gantt"]');
  if (!ganttTab || !ganttTab.classList.contains("active")) return;
  const tag = document.activeElement.tagName;
  if (tag === "INPUT" || tag === "SELECT" || tag === "TEXTAREA") return;

  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "z") {
    e.preventDefault();
    if (ganttUndo) { const fn = ganttUndo; ganttUndo = null; fn(); }
    return;
  }
  if (ganttSelectedId == null) return;
  const id = ganttSelectedId;
  const t = taskById(id);
  if (!t) return;

  if (e.key === "Enter") { e.preventDefault(); ganttAddTask(t.parent_id || null); }
  else if (e.key === "Tab" && !e.shiftKey) { e.preventDefault(); ganttPromoteOrIndent(id, "indent"); }
  else if (e.key === "Tab" && e.shiftKey) { e.preventDefault(); ganttPromoteOrIndent(id, "promote"); }
  else if (e.key === "Delete" || e.key === "Backspace") { e.preventDefault(); ganttDeleteTask(id); }
  else if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "d") { e.preventDefault(); ganttDuplicateTask(id); }
});

function wireGanttEvents(rows, schedule, pxPerDay) {
  document.getElementById("gantt-add-top").onclick = () => ganttAddTask(null);
  document.getElementById("gantt-expand-all").onclick = () => { ganttCollapsed.clear(); saveGanttCollapsed(); renderGanttView(); };
  document.getElementById("gantt-collapse-all").onclick = () => {
    rows.forEach((r) => { if (r.hasKids) ganttCollapsed.add(r.task.id); });
    saveGanttCollapsed(); renderGanttView();
  };
  document.querySelectorAll(".gantt-zoom button").forEach((b) => {
    b.onclick = () => { ganttZoom = b.dataset.zoom; localStorage.setItem("pmo_gantt_zoom", ganttZoom); renderGanttView(); };
  });
  document.getElementById("gantt-critical-toggle").onclick = () => { ganttShowCritical = !ganttShowCritical; renderGanttView(); };
  document.getElementById("gantt-export-btn").onclick = () => { window.location.href = "/api/gantt/export.xlsx"; };
  document.getElementById("gantt-import-btn").onclick = () => document.getElementById("gantt-import-file").click();
  document.getElementById("gantt-import-file").onchange = async (e) => {
    const file = e.target.files[0];
    e.target.value = "";
    if (!file) return;
    const formData = new FormData();
    formData.append("file", file);
    const r = await fetch("/api/gantt/import/preview", { method: "POST", body: formData });
    const data = await r.json();
    if (!r.ok) { showToast(data.error || "Could not read file"); return; }
    ganttShowImportModal(data);
  };

  const bulkOwnerApply = document.getElementById("gantt-bulk-owner-apply");
  if (bulkOwnerApply) {
    bulkOwnerApply.onclick = async () => {
      const owner = document.getElementById("gantt-bulk-owner").value;
      if (!owner) return;
      await fetch("/api/bulk", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ids: [...ganttMultiSelected], fields: { owner } }) });
      await loadTasks(); renderGanttView();
    };
    document.getElementById("gantt-bulk-status-apply").onclick = async () => {
      const label = document.getElementById("gantt-bulk-status").value;
      for (const id of ganttMultiSelected) await ganttSetStatus(id, label);
      renderGanttView();
    };
    document.getElementById("gantt-bulk-clear").onclick = () => { ganttMultiSelected.clear(); renderGanttView(); };
  }

  document.querySelectorAll("[data-caret]").forEach((el) => {
    el.onclick = (e) => {
      e.stopPropagation();
      const id = parseInt(el.dataset.caret);
      if (ganttCollapsed.has(id)) ganttCollapsed.delete(id); else ganttCollapsed.add(id);
      saveGanttCollapsed(); renderGanttView();
    };
  });
  document.querySelectorAll("[data-add-child]").forEach((el) => {
    el.onclick = (e) => { e.stopPropagation(); ganttAddTask(parseInt(el.dataset.addChild)); };
  });
  document.querySelectorAll("[data-gantt-menu]").forEach((el) => {
    el.onclick = (e) => { e.stopPropagation(); ganttOpenRowMenu(el, parseInt(el.dataset.ganttMenu)); };
  });
  document.querySelectorAll("[data-status]").forEach((el) => {
    el.onchange = () => ganttSetStatus(parseInt(el.dataset.status), el.value);
    el.onclick = (e) => e.stopPropagation();
  });
  document.querySelectorAll(".gantt-cell.editable, .gantt-cell-text.editable").forEach((el) => {
    el.onclick = (e) => { e.stopPropagation(); ganttStartEdit(el, parseInt(el.dataset.id), el.dataset.edit); };
  });

  document.querySelectorAll("[data-gantt-row]").forEach((el) => {
    const id = parseInt(el.dataset.ganttRow);
    el.addEventListener("click", (e) => {
      if (e.ctrlKey || e.metaKey || e.shiftKey) {
        if (ganttMultiSelected.has(id)) ganttMultiSelected.delete(id); else ganttMultiSelected.add(id);
      } else {
        ganttMultiSelected.clear();
        ganttSelectedId = ganttSelectedId === id ? null : id;
      }
      renderGanttView();
    });
    el.addEventListener("dragstart", (e) => { ganttDragId = id; el.style.opacity = "0.4"; if (e.dataTransfer) e.dataTransfer.effectAllowed = "move"; });
    el.addEventListener("dragend", () => { el.style.opacity = "1"; });
    el.addEventListener("dragover", (e) => {
      e.preventDefault();
      if (ganttDragId === id) return;
      const rect = el.getBoundingClientRect();
      const frac = (e.clientY - rect.top) / rect.height;
      el.classList.remove("drop-before", "drop-after", "drop-child");
      if (frac < 0.25) el.classList.add("drop-before");
      else if (frac > 0.75) el.classList.add("drop-after");
      else el.classList.add("drop-child");
    });
    el.addEventListener("dragleave", () => el.classList.remove("drop-before", "drop-after", "drop-child"));
    el.addEventListener("drop", (e) => {
      e.preventDefault();
      const mode = el.classList.contains("drop-before") ? "before" : el.classList.contains("drop-after") ? "after" : "child";
      el.classList.remove("drop-before", "drop-after", "drop-child");
      ganttHandleRowDrop(ganttDragId, id, mode);
    });
  });

  document.querySelectorAll("[data-bar]").forEach((g) => {
    const id = parseInt(g.dataset.bar);
    const shape = g.querySelector(".gantt-bar, .gantt-milestone-shape");
    if (!shape) return;
    shape.addEventListener("mousedown", (e) => {
      if (e.button !== 0) return;
      e.preventDefault(); e.stopPropagation();
      const startClientX = e.clientX;
      let deltaDays = 0;
      const onMove = (ev) => {
        deltaDays = Math.round((ev.clientX - startClientX) / pxPerDay);
        g.style.transform = deltaDays ? `translateX(${deltaDays * pxPerDay}px)` : "";
      };
      const onUp = () => {
        document.removeEventListener("mousemove", onMove);
        document.removeEventListener("mouseup", onUp);
        g.style.transform = "";
        if (deltaDays === 0) {
          ganttMultiSelected.clear();
          ganttSelectedId = ganttSelectedId === id ? null : id;
          renderGanttView();
          return;
        }
        const s = schedule.get(id);
        const newStart = GanttEngine.toISO(GanttEngine.addDays(GanttEngine.parseISO(s.start), deltaDays));
        const newFinish = GanttEngine.toISO(GanttEngine.addDays(GanttEngine.parseISO(s.finish), deltaDays));
        ganttShowRescheduleConfirm(GanttEngine.rescheduleFrom(TASKS, id, newStart, newFinish), id);
      };
      document.addEventListener("mousemove", onMove);
      document.addEventListener("mouseup", onUp);
    });
  });

  document.querySelectorAll("[data-resize]").forEach((handle) => {
    const id = parseInt(handle.dataset.resize);
    const rect = handle.closest("[data-bar]").querySelector("rect.gantt-bar");
    if (!rect) return;
    const origWidth = parseFloat(rect.getAttribute("width"));
    const origX = parseFloat(rect.getAttribute("x"));
    handle.addEventListener("mousedown", (e) => {
      if (e.button !== 0) return;
      e.preventDefault(); e.stopPropagation();
      const startClientX = e.clientX;
      let deltaDays = 0;
      const onMove = (ev) => {
        deltaDays = Math.round((ev.clientX - startClientX) / pxPerDay);
        const newW = Math.max(origWidth + deltaDays * pxPerDay, 3);
        rect.setAttribute("width", newW);
        handle.setAttribute("x", origX + newW - 5);
      };
      const onUp = () => {
        document.removeEventListener("mousemove", onMove);
        document.removeEventListener("mouseup", onUp);
        if (deltaDays === 0) return;
        const s = schedule.get(id);
        const newFinish = GanttEngine.toISO(GanttEngine.addDays(GanttEngine.parseISO(s.finish), deltaDays));
        if (GanttEngine.diffDays(GanttEngine.parseISO(newFinish), GanttEngine.parseISO(s.start)) < 0) { renderGanttView(); return; }
        ganttShowRescheduleConfirm(GanttEngine.rescheduleFrom(TASKS, id, s.start, newFinish), id);
      };
      document.addEventListener("mousemove", onMove);
      document.addEventListener("mouseup", onUp);
    });
  });

  const tableY = document.getElementById("gantt-table-scroll-y");
  const timelineY = document.getElementById("gantt-timeline-scroll-y");
  let syncing = false;
  tableY.addEventListener("scroll", () => { if (syncing) return; syncing = true; timelineY.scrollTop = tableY.scrollTop; syncing = false; });
  timelineY.addEventListener("scroll", () => { if (syncing) return; syncing = true; tableY.scrollTop = timelineY.scrollTop; syncing = false; });
}
