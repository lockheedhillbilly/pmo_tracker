const CURRENT_USER = window.CURRENT_USER;
const MODULE_COLORS = {
  "Account Prioritization": "#2E8B57", "Account Intelligence": "#2C6E9B",
  "Seller Copilot": "#7A4FBE", "Overall UI": "#D98B3B",
};
const moduleColor = m => MODULE_COLORS[m] || "#6B7280";
const WEEK1_MONDAY = new Date("2026-07-13T00:00:00");
const AVATAR_COLORS = ["#2E8B57","#2C6E9B","#7A4FBE","#D98B3B","#C0392B","#0E7C7B","#8E5B3C"];
const ALL_COLUMNS = {
  module: { label: "Use case", width: "130px" },
  owner:  { label: "Owner", width: "110px" },
  task:   { label: "Task", width: "auto" },
  added:  { label: "Added", width: "70px" },
  due:    { label: "Due", width: "80px" },
  priority: { label: "Pri.", width: "34px" },
  execution_state: { label: "Execution", width: "100px" },
  review: { label: "Review", width: "72px" },
  notes:  { label: "Notes", width: "150px" },
  updated_at: { label: "Updated", width: "90px" },
};
const DEFAULT_COLUMNS = ["module","owner","task","added","due","priority","execution_state","review","notes"];

let TASKS = [], NOTE_COUNTS = {};
let selected = new Set();
let activeRowId = null;
let filters = { owner: null, module: null, priority: null, due: "all", status: "all", moduleBlank: false };
let quickFilter = null;
let searchQ = "";
let sortCol = null, sortDir = 1;
let hideCompleted = false;
let density = localStorage.getItem("pmo_density") || "comfortable";
let viewBy = "module", thenBy = "none";
let collapsedGroups = new Set();
let activeGroupAddRows = new Set();
let columns = loadColumnsForView(viewBy);
let columnWidths = JSON.parse(localStorage.getItem("pmo_col_widths_v2") || "{}");
let addDefaults = { owner: "", module: "", track: "Discovery", due: tomorrow() };
let dragRowId = null, dragCol = null;

function loadColumnsForView(vb) {
  const raw = localStorage.getItem("pmo_cols_v2_" + vb);
  return raw ? JSON.parse(raw) : DEFAULT_COLUMNS.slice();
}
function saveColumnsForView() { localStorage.setItem("pmo_cols_v2_" + viewBy, JSON.stringify(columns)); }

function today() { return new Date().toISOString().slice(0,10); }
function fmtDateShort(d) { return new Date(d+"T00:00:00").toLocaleDateString('en-GB', {day:'numeric', month:'short'}); }
function fmtDateTimeFull(iso) { return new Date(iso).toLocaleString('en-GB', {day:'numeric', month:'short', year:'numeric', hour:'numeric', minute:'2-digit'}); }
function weekOf(dateStr) { const d = new Date(dateStr+"T00:00:00"); return Math.floor((d - WEEK1_MONDAY) / (7*86400000)) + 1; }
function esc(s) { return (s==null?"":String(s)).replace(/</g,"&lt;"); }
function isOverdue(t) { return t.status === "Open" && t.due < today(); }
function isNew(t) {
  // "added" is date-only in the data model, so this approximates a 24h window
  // as "added today or yesterday" rather than an exact hour-level cutoff.
  const diffDays = (new Date(today()+"T00:00:00") - new Date(t.added+"T00:00:00")) / 86400000;
  return diffDays <= 1;
}
function isDueSoon(t) {
  if (t.status !== "Open") return false;
  const diff = (new Date(t.due+"T00:00:00") - new Date(today()+"T00:00:00"))/86400000;
  return diff >= 0 && diff <= 1;
}
function avatarColor(name) { let h=0; for (const c of name) h = (h*31 + c.charCodeAt(0)) % AVATAR_COLORS.length; return AVATAR_COLORS[h]; }
function initials(name) { return name.split(/\s+/).filter(Boolean).slice(0,2).map(w=>w[0].toUpperCase()).join(""); }
function collaboratorList(t) { return (t.collaborators || "").split(",").map(s => s.trim()).filter(Boolean); }
function isMine(t) { return t.owner === CURRENT_USER || collaboratorList(t).includes(CURRENT_USER); }

function showToast(msg, undo) {
  const t = document.getElementById("toast");
  t.innerHTML = esc(msg) + (undo ? ` &nbsp; <a id="toast-undo">Undo</a>` : "");
  t.style.display = "flex";
  if (undo) document.getElementById("toast-undo").onclick = () => { undo(); t.style.display = "none"; };
  clearTimeout(t._timer);
  t._timer = setTimeout(() => { t.style.display = "none"; }, 7000);
  return () => { t.style.display = "none"; };
}
function closePopovers() { document.querySelectorAll(".popover, .filter-panel").forEach(p => p.remove()); }
document.addEventListener("click", (e) => {
  if (!e.target.closest(".popover") && !e.target.closest(".filter-panel") && !e.target.closest(".filt") && !e.target.closest(".rowmenu-btn") && !e.target.closest(".review-flag") && !e.target.closest(".review-chip")) {
    closePopovers();
  }
});

async function loadMeta() { window.META = await (await fetch("/api/meta")).json(); }
async function loadTasks() {
  TASKS = await (await fetch("/api/tasks")).json();
  NOTE_COUNTS = await (await fetch("/api/note_counts")).json();
  renderAll();
}
async function patchTask(id, fields) {
  const r = await fetch(`/api/tasks/${id}`, {method:"PATCH", headers:{"Content-Type":"application/json"}, body: JSON.stringify(fields)});
  const updated = await r.json();
  const idx = TASKS.findIndex(t => t.id === id);
  if (idx >= 0) TASKS[idx] = updated;
  return updated;
}
function taskById(id) { return TASKS.find(t => t.id === id); }

function renderAll() { buildHeader(); renderSummary(); renderBoard(); }

// ---------- Summary + quick filters ----------
function renderSummary() {
  const t0 = today();
  const open = TASKS.filter(t => t.status === "Open").length;
  const overdue = TASKS.filter(isOverdue).length;
  const mon = new Date(t0); mon.setDate(mon.getDate() - ((mon.getDay()+6)%7));
  const sun = new Date(mon); sun.setDate(mon.getDate()+6);
  const dueWeek = TASKS.filter(t => t.status === "Open" && t.due >= mon.toISOString().slice(0,10) && t.due <= sun.toISOString().slice(0,10)).length;
  const done = TASKS.filter(t => t.status === "Done").length;
  const myReviews = TASKS.filter(t => t.reviewer === CURRENT_USER && t.review_status).length;
  const metrics = [["open","Open",open],["overdue","Overdue",overdue],["dueWeek","Due this week",dueWeek],["completed","Completed",done],["myReviews","My reviews",myReviews]];
  document.getElementById("summary").innerHTML = metrics.map(([key,label,val]) =>
    `<div class="metric m-${key} ${quickFilter===key?'active':''}" data-qf="${key}"><b>${val}</b>${label}</div>`
  ).join("");
  document.querySelectorAll(".metric").forEach(el => el.onclick = () => setQuickFilter(el.dataset.qf));

  const cw = weekOf(today());
  const chips = [["myTasks","My tasks"],["unassigned","Unassigned"], ["noDue","No due date"], ["highPriority","High priority"], ["recentlyUpdated","Recently updated"]];
  document.getElementById("qfilters").innerHTML = `<span class="qchip" style="cursor:default;">Sprint/Week: Week ${cw}</span>` +
    chips.map(([key,label]) => `<span class="qchip ${quickFilter===key?'active':''}" data-qf="${key}">${label}</span>`).join("");
  document.querySelectorAll(".qchip[data-qf]").forEach(el => el.onclick = () => setQuickFilter(el.dataset.qf));
}
function setQuickFilter(key) { quickFilter = (quickFilter === key) ? null : key; renderSummary(); renderBoard(); }

// ---------- Filtering ----------
function applyFilters(list) {
  const t0 = today();
  const weekAgo = new Date(Date.now() - 7*86400000).toISOString();
  return list.filter(t => {
    if (filters.owner && !filters.owner.has(t.owner)) return false;
    if (filters.moduleBlank && t.module) return false;
    if (!filters.moduleBlank && filters.module && !filters.module.has(t.module || "(none)")) return false;
    if (filters.priority && !filters.priority.has(t.priority)) return false;
    if (filters.status === "Open" && t.status !== "Open") return false;
    if (filters.status === "Done" && t.status !== "Done") return false;
    if (filters.due === "overdue" && !isOverdue(t)) return false;
    if (filters.due === "today" && t.due !== t0) return false;
    if (filters.due === "week") {
      const mon = new Date(t0); mon.setDate(mon.getDate() - ((mon.getDay()+6)%7));
      const sun = new Date(mon); sun.setDate(mon.getDate()+6);
      if (!(t.due >= mon.toISOString().slice(0,10) && t.due <= sun.toISOString().slice(0,10))) return false;
    }
    if (hideCompleted && t.status === "Done") return false;
    if (quickFilter === "overdue" && !isOverdue(t)) return false;
    if (quickFilter === "dueWeek") {
      const mon = new Date(t0); mon.setDate(mon.getDate() - ((mon.getDay()+6)%7));
      const sun = new Date(mon); sun.setDate(mon.getDate()+6);
      if (!(t.status==="Open" && t.due >= mon.toISOString().slice(0,10) && t.due <= sun.toISOString().slice(0,10))) return false;
    }
    if (quickFilter === "completed" && t.status !== "Done") return false;
    if (quickFilter === "myReviews" && !(t.reviewer === CURRENT_USER && t.review_status)) return false;
    if (quickFilter === "myTasks" && !isMine(t)) return false;
    if (quickFilter === "unassigned" && t.owner !== "Unassigned") return false;
    if (quickFilter === "noDue" && t.due) return false;
    if (quickFilter === "highPriority" && t.priority !== "High") return false;
    if (quickFilter === "recentlyUpdated" && !(t.updated_at >= weekAgo)) return false;
    if (searchQ && !(t.task.toLowerCase().includes(searchQ) || t.owner.toLowerCase().includes(searchQ) || collaboratorList(t).join(" ").toLowerCase().includes(searchQ) || (t.module||"").toLowerCase().includes(searchQ))) return false;
    return true;
  });
}
function activeFilterChips() {
  const chips = [];
  if (filters.owner) chips.push(["owner", `Owner: ${[...filters.owner].join(', ')}`]);
  if (filters.moduleBlank) chips.push(["module", "Use case: (blank)"]);
  else if (filters.module) chips.push(["module", `Use case: ${[...filters.module].join(', ')}`]);
  if (filters.priority) chips.push(["priority", `Priority: ${[...filters.priority].join(', ')}`]);
  if (filters.status !== "all") chips.push(["status", `Status: ${filters.status}`]);
  if (filters.due !== "all") chips.push(["due", `Due: ${filters.due}`]);
  return chips;
}
function renderChips() {
  const chips = activeFilterChips();
  document.getElementById("chips").innerHTML = chips.map(([key,label]) =>
    `<span class="chip">${esc(label)} <a data-clear="${key}">&times;</a></span>`
  ).join("");
  document.querySelectorAll(".chip a").forEach(a => a.onclick = () => {
    const key = a.dataset.clear;
    if (key === "module") { filters.module = null; filters.moduleBlank = false; }
    else if (key === "due") filters.due = "all";
    else if (key === "status") filters.status = "all";
    else filters[key] = null;
    markFilterIcon(); renderBoard();
  });
  document.getElementById("filters-btn").textContent = chips.length ? `Filters (${chips.length})` : "Filters";
}

// ---------- Grouping ----------
function groupKey(t, mode) {
  if (mode === "module") return t.module || "(no use case)";
  if (mode === "owner") return t.owner;
  if (mode === "week") return `Week ${weekOf(t.added)}`;
  if (mode === "status") return t.status;
  if (mode === "review") return t.review_status || "No review";
  return null;
}
function groupFieldForViewBy() {
  // only module/owner map to a single writable field — week/status/review are
  // derived or already have their own dedicated one-click controls.
  return viewBy === "module" ? "module" : viewBy === "owner" ? "owner" : null;
}
function sortRowsWithinGroup(list) {
  let arr = list.slice();
  if (sortCol) arr.sort((a,b) => { const va=a[sortCol]??"", vb=b[sortCol]??""; return va<vb?-sortDir:va>vb?sortDir:0; });
  const overdueRows = arr.filter(isOverdue), rest = arr.filter(t => !isOverdue(t));
  return [...overdueRows, ...rest];
}

// ---------- Header ----------
function visibleColumns() {
  // don't show a column that duplicates the current grouping — the group headers already say it
  return columns.filter(c => c !== viewBy);
}
function colWidth(key) { return columnWidths[key] || ALL_COLUMNS[key].width; }

function buildHeader() {
  let html = `<th class="done-cell"><span class="filt" data-col="status">&#9660;</span></th>`;
  html += `<th style="width:32px;text-align:center;">#</th>`;
  visibleColumns().forEach(key => {
    const def = ALL_COLUMNS[key];
    html += `<th class="draggable-col" draggable="true" data-colkey="${key}" style="width:${colWidth(key)};">
      <span class="htxt" data-sort="${key}">${def.label}</span>
      ${["task","notes"].includes(key) ? "" : `<span class="filt" data-col="${key}">&#9660;</span>`}
      <span class="col-resizer" data-resize-col="${key}"></span>
    </th>`;
  });
  html += `<th style="width:44px;"></th>`;
  document.getElementById("header-row").innerHTML = html;
  markFilterIcon();

  document.querySelectorAll("th .htxt").forEach(el => {
    el.onclick = () => { const col = el.dataset.sort; sortDir = (sortCol===col)?-sortDir:1; sortCol=col; renderBoard(); };
  });
  document.querySelectorAll("th .filt").forEach(el => {
    el.onclick = (e) => { e.stopPropagation(); closePopovers(); openFilterPanel(el, el.dataset.col); };
  });
  document.querySelectorAll(".col-resizer").forEach(handle => {
    handle.addEventListener("mousedown", (e) => {
      e.preventDefault(); e.stopPropagation();
      const key = handle.dataset.resizeCol;
      const th = handle.closest("th");
      th.draggable = false; // avoid fighting with column drag-reorder while resizing
      const startX = e.clientX, startWidth = th.getBoundingClientRect().width;
      function onMove(ev) {
        const w = Math.max(50, Math.round(startWidth + (ev.clientX - startX)));
        th.style.width = w + "px";
      }
      function onUp() {
        columnWidths[key] = th.style.width;
        localStorage.setItem("pmo_col_widths_v2", JSON.stringify(columnWidths));
        th.draggable = true;
        document.removeEventListener("mousemove", onMove);
        document.removeEventListener("mouseup", onUp);
      }
      document.addEventListener("mousemove", onMove);
      document.addEventListener("mouseup", onUp);
    });
    handle.addEventListener("click", (e) => e.stopPropagation());
  });
  document.querySelectorAll("th.draggable-col").forEach(th => {
    th.addEventListener("dragstart", () => { dragCol = th.dataset.colkey; });
    th.addEventListener("dragover", (e) => e.preventDefault());
    th.addEventListener("drop", (e) => {
      e.preventDefault();
      const target = th.dataset.colkey;
      if (!dragCol || dragCol === target) return;
      const from = columns.indexOf(dragCol), to = columns.indexOf(target);
      columns.splice(to, 0, columns.splice(from,1)[0]);
      saveColumnsForView();
      buildHeader(); renderBoard();
    });
  });
}

// ---------- Cell rendering ----------
function renderCell(t, key) {
  if (key === "module") return `<td class="editable-cell" data-field="module"><span class="badge" style="background:${moduleColor(t.module)}22;color:${moduleColor(t.module)};">${esc(t.module||t.track)}</span></td>`;
  if (key === "owner") {
    const collabHtml = collaboratorList(t).map(c =>
      `<span class="avatar collab-avatar" style="background:${avatarColor(c)};" title="${esc(c)} (collaborator)">${esc(initials(c))}</span>`
    ).join("");
    return `<td class="editable-cell owner-cell" data-field="owner"><span class="avatar" style="background:${avatarColor(t.owner)};">${esc(initials(t.owner))}</span>${esc(t.owner)}${collabHtml}</td>`;
  }
  if (key === "task") {
    const newBadge = isNew(t) ? `<span class="badge new-badge">New</span> ` : "";
    return `<td class="editable-cell task-cell" data-field="task">${newBadge}<span class="task-text" title="${esc(t.task)}">${esc(t.task)}</span></td>`;
  }
  if (key === "added") return `<td><span class="added-text" title="${esc(fmtDateTimeFull(t.added+'T00:00:00'))} · Added by ${esc(t.owner)}">${fmtDateShort(t.added)}</span></td>`;
  if (key === "due") {
    const cls = isOverdue(t) ? "overdue" : isDueSoon(t) ? "soon" : "";
    const label = isOverdue(t) ? `${fmtDateShort(t.due)} (overdue)` : fmtDateShort(t.due);
    return `<td class="editable-cell" data-field="due"><span class="due-text ${cls}">${label}</span></td>`;
  }
  if (key === "priority") return `<td><span class="priority-flag ${t.priority==='High'?'high':''}" data-priority-for="${t.id}" title="${t.priority==='High'?'High priority — click to clear':'Click to mark High priority'}">${t.priority==='High'?'⚑':'⚐'}</span></td>`;
  if (key === "execution_state") {
    if (!t.execution_state) return `<td class="editable-cell" data-field="execution_state"></td>`;
    const stateColors = {"Not started":"#6B7280","In progress":"#2C6E9B","Blocked":"#C0392B"};
    const c = stateColors[t.execution_state] || "#6B7280";
    return `<td class="editable-cell" data-field="execution_state"><span class="badge" style="background:${c}22;color:${c};">${esc(t.execution_state)}</span></td>`;
  }
  if (key === "review") return `<td>${renderReviewCell(t)}</td>`;
  if (key === "notes") {
    const info = NOTE_COUNTS[t.id];
    const preview = info ? info.latest : "";
    const more = info && info.count > 1 ? `<span class="notes-more" data-notes-more="${t.id}">+${info.count-1} more</span>` : "";
    return `<td><textarea class="notes-inline" data-notes-for="${t.id}" rows="1" placeholder="Add a note...">${esc(preview)}</textarea>${more}</td>`;
  }
  if (key === "updated_at") return `<td><span class="added-text" title="${esc(fmtDateTimeFull(t.updated_at))}">${fmtDateShort(t.updated_at.slice(0,10))}</span></td>`;
  return "<td></td>";
}
function renderReviewCell(t) {
  if (!t.review_status) return `<span class="review-flag" data-review-for="${t.id}">&#9872;</span>`;
  const short = t.review_status === "Review pending" ? ["pending","Pending"] : t.review_status === "Changes requested" ? ["changes","Changes"] : ["reviewed","Reviewed"];
  return `<span class="review-chip ${short[0]}" data-review-for="${t.id}" title="Reviewer: ${esc(t.reviewer)}">${short[1]}</span>`;
}

let snoCounter = 0;
function renderDataRow(t) {
  const rowClasses = ["data-row"];
  if (isOverdue(t)) rowClasses.push("overdue-row");
  if (t.status === "Done") rowClasses.push("done-row");
  if (selected.has(t.id)) rowClasses.push("selected");
  if (activeRowId === t.id) rowClasses.push("active-row");
  const cells = visibleColumns().map(key => renderCell(t, key)).join("");
  snoCounter += 1;
  return `<tr class="${rowClasses.join(' ')}" data-id="${t.id}" draggable="true" tabindex="0">
    <td class="done-cell"><div class="done-hit"><input type="checkbox" class="done-chk" ${t.status==='Done'?'checked':''}></div></td>
    <td style="text-align:center;color:var(--mute);font-size:11px;">${snoCounter}</td>
    ${cells}
    <td><button class="rowmenu-btn" data-menu-for="${t.id}">&#8942;</button></td>
  </tr>`;
}

function renderGroupedBoard(list) {
  const groups = {};
  list.forEach(t => { const k = groupKey(t, viewBy); (groups[k] = groups[k] || []).push(t); });
  let html = "";
  Object.keys(groups).sort().forEach(g1key => {
    const g1tasks = groups[g1key];
    const open = g1tasks.filter(t=>t.status==="Open").length, done = g1tasks.filter(t=>t.status==="Done").length;
    const reviewPending = g1tasks.filter(t=>t.review_status==="Review pending").length;
    const overdueN = g1tasks.filter(isOverdue).length;
    const collapsed = collapsedGroups.has(g1key);
    let meta = `${open} open`;
    if (done) meta += ` &middot; ${done} done`;
    if (reviewPending) meta += ` &middot; ${reviewPending} review pending`;
    if (overdueN) meta += ` &middot; ${overdueN} overdue`;
    html += `<tr class="group-header" data-group="${esc(g1key)}"><td colspan="${visibleColumns().length+3}">
      <span class="ghactions">
        <span class="ghlink" data-sortgroup="${esc(g1key)}">Sort</span>
        <span class="ghadd" data-addto="${esc(g1key)}">+ Add task</span>
      </span>
      ${collapsed?'&#9656;':'&#9662;'} ${esc(g1key)} <span class="ghmeta">${meta}</span>
    </td></tr>`;
    if (activeGroupAddRows.has(g1key)) html += addRowHtml({groupKey: g1key});
    if (collapsed) return;
    if (thenBy !== "none") {
      const subgroups = {};
      g1tasks.forEach(t => { const k = groupKey(t, thenBy); (subgroups[k] = subgroups[k] || []).push(t); });
      Object.keys(subgroups).sort().forEach(g2key => {
        const g2tasks = sortRowsWithinGroup(subgroups[g2key]);
        const subKey = g1key + "::" + g2key;
        const subCollapsed = collapsedGroups.has(subKey);
        html += `<tr class="subgroup-header" data-group="${esc(subKey)}"><td colspan="${visibleColumns().length+3}">${subCollapsed?'&#9656;':'&#9662;'} ${esc(g2key)} (${g2tasks.length})</td></tr>`;
        if (!subCollapsed) html += g2tasks.map(renderDataRow).join("");
      });
    } else {
      html += sortRowsWithinGroup(g1tasks).map(renderDataRow).join("");
    }
  });
  return html;
}

function renderBoard() {
  snoCounter = 0;
  renderChips();
  let list = applyFilters(TASKS);
  const tbody = document.getElementById("task-rows");
  let bodyHtml;
  if (viewBy === "flat") {
    bodyHtml = (activeGroupAddRows.has("__top__") ? "" : "") + sortRowsWithinGroup(list).map(renderDataRow).join("");
  } else {
    bodyHtml = renderGroupedBoard(list);
  }
  tbody.innerHTML = addRowHtml({groupKey: null}) + bodyHtml;
  attachRowHandlers();
  attachAddRowHandlers();
  attachGroupHandlers();
  attachCellIconHandlers();
  document.getElementById("board-table").className = density;
}

function attachGroupHandlers() {
  document.querySelectorAll(".group-header, .subgroup-header").forEach(tr => {
    tr.addEventListener("click", (e) => {
      if (e.target.closest(".ghadd") || e.target.closest(".ghlink")) return;
      const key = tr.dataset.group;
      if (collapsedGroups.has(key)) collapsedGroups.delete(key); else collapsedGroups.add(key);
      renderBoard();
    });
  });
  document.querySelectorAll(".ghadd").forEach(el => {
    el.addEventListener("click", (e) => { e.stopPropagation(); const g = el.dataset.addto; openGroupAddRow(g); });
  });
  document.querySelectorAll(".ghlink[data-sortgroup]").forEach(el => {
    el.addEventListener("click", (e) => { e.stopPropagation(); sortCol = "due"; sortDir = 1; renderBoard(); });
  });
  document.querySelectorAll(".group-header").forEach(tr => {
    tr.addEventListener("dragover", (e) => e.preventDefault());
    tr.addEventListener("drop", async (e) => {
      e.preventDefault();
      const field = groupFieldForViewBy();
      if (!field || dragRowId === null) return;
      const groupValue = tr.dataset.group === "(no use case)" ? "" : tr.dataset.group;
      const dragged = taskById(dragRowId);
      if (dragged[field] === groupValue || (!dragged[field] && !groupValue)) return;
      await patchTask(dragRowId, { [field]: groupValue });
      showToast(`Moved to ${tr.dataset.group}`);
      await loadTasks();
    });
  });
}

function contextDefaultsForGroup(g1key) {
  const d = { owner: "", module: "", track: "Discovery", due: tomorrow() };
  if (viewBy === "module") d.module = g1key === "(no use case)" ? "" : g1key;
  else if (viewBy === "owner") d.owner = g1key;
  else if (viewBy === "review") d.owner = "";
  else if (viewBy === "week") { /* due prefilled to a date within that week */ }
  return d;
}
function openGroupAddRow(g1key) {
  activeGroupAddRows.add(g1key);
  Object.assign(addDefaults, contextDefaultsForGroup(g1key));
  renderBoard();
  const inp = document.querySelector(`tr[data-addrow-for="${CSS.escape(g1key)}"] .task-input`);
  if (inp) { inp.scrollIntoView({block:"center"}); inp.focus(); }
}

// ---------- Add row ----------
// Cells are built from visibleColumns(), in the SAME order as the header and
// data rows — this must never hardcode a fixed [module, owner, task] prefix,
// since that silently drifts out of alignment whenever a column is hidden
// (e.g. grouped by use case, so the use-case column itself isn't shown) or
// reordered. Fields with no dedicated input just render an empty <td>, and
// their value comes from the group context / last-used default instead.
function addRowHtml(ctx) {
  const groupKey = ctx.groupKey;
  const marker = groupKey === null ? "__top__" : groupKey;
  const d = groupKey === null ? addDefaults : Object.assign(contextDefaultsForGroup(groupKey), addDefaults);
  const cellFor = key => {
    if (key === "module") return `<td><input class="add-module" placeholder="Use case" value="${esc(d.module)}" list="module-list"></td>`;
    if (key === "owner") return `<td><input class="add-owner" placeholder="Owner" value="${esc(d.owner)}" list="owner-list"></td>`;
    if (key === "task") return `<td><input class="add-task task-input" placeholder="Type task, Enter to add & continue, Ctrl+Enter to close, or paste multiple lines..."></td>`;
    if (key === "due") return `<td><input type="date" class="add-due" value="${esc(d.due)}"></td>`;
    return "<td></td>";
  };
  const cells = visibleColumns().map(cellFor).join("");
  return `<tr class="addrow" data-addrow-for="${esc(marker)}">
    <td class="done-cell"></td>
    <td></td>
    ${cells}
    <td><button type="button" class="small add-confirm-btn" title="Add task">Add</button></td>
  </tr>
  <datalist id="module-list">${(window.META?.modules||[]).map(m=>`<option value="${esc(m)}">`).join("")}</datalist>
  <datalist id="owner-list">${(window.META?.owners||[]).map(o=>`<option value="${esc(o)}">`).join("")}</datalist>`;
}

function tomorrow() {
  // Parse/advance/format all in UTC — today() is itself a UTC calendar date (via
  // toISOString()), so mixing in local-time Date methods here would silently shift
  // the result by a day whenever the local UTC offset (e.g. IST, +5:30) crosses
  // midnight relative to UTC.
  const d = new Date(today() + "T00:00:00Z");
  d.setUTCDate(d.getUTCDate() + 1);
  return d.toISOString().slice(0,10);
}

function attachAddRowHandlers() {
  document.querySelectorAll(".addrow").forEach(row => {
    const marker = row.dataset.addrowFor;
    const taskInput = row.querySelector(".add-task");
    const ownerInput = row.querySelector(".add-owner");
    const moduleInput = row.querySelector(".add-module");
    const dueInput = row.querySelector(".add-due");
    const confirmBtn = row.querySelector(".add-confirm-btn");
    if (!taskInput) return;

    const currentOwner = () => ownerInput ? ownerInput.value : addDefaults.owner;
    const currentModule = () => moduleInput ? moduleInput.value : addDefaults.module;
    const currentDue = () => dueInput ? dueInput.value : addDefaults.due;

    // Owner is required before a task can be created: rather than silently falling back
    // to "Unassigned" (which felt like the typed task text got thrown away), block the
    // submit, flag the owner field, and leave the task text exactly as typed.
    const trySubmit = async (closeAfter) => {
      if (!taskInput.value.trim()) return;
      if (ownerInput && !ownerInput.value.trim()) {
        ownerInput.classList.add("input-error");
        ownerInput.focus();
        showToast("Add an owner before creating the task");
        return;
      }
      if (ownerInput) ownerInput.classList.remove("input-error");
      await submitAdd(taskInput.value.trim(), currentOwner(), currentModule(), currentDue(), marker);
      if (closeAfter && marker !== "__top__") { activeGroupAddRows.delete(marker); renderBoard(); }
    };

    taskInput.addEventListener("keydown", (e) => {
      if (e.key === "Escape") {
        if (marker !== "__top__") { activeGroupAddRows.delete(marker); renderBoard(); }
        else taskInput.value = "";
        return;
      }
      if (e.key === "Enter" && taskInput.value.trim()) trySubmit(e.ctrlKey || e.metaKey);
    });
    taskInput.addEventListener("paste", (e) => {
      const text = e.clipboardData.getData("text");
      if (text.includes("\n")) { e.preventDefault(); parsePasted(text, currentOwner(), currentModule()); }
    });
    if (ownerInput) {
      ownerInput.addEventListener("input", () => ownerInput.classList.remove("input-error"));
      ownerInput.addEventListener("keydown", (e) => { if (e.key === "Enter" && taskInput.value.trim()) trySubmit(false); });
    }
    if (confirmBtn) confirmBtn.addEventListener("click", () => trySubmit(false));
  });
}

async function submitAdd(task, owner, module, due, marker) {
  addDefaults = { owner, module, track: addDefaults.track, due };
  await fetch("/api/tasks", {method:"POST", headers:{"Content-Type":"application/json"}, body: JSON.stringify({
    task, owner: owner || "Unassigned", module: module || null, track: addDefaults.track, due: due || null, priority: "Normal",
  })});
  await loadTasks();
  const sel = marker === "__top__" ? ".addrow[data-addrow-for='__top__'] .task-input" : `.addrow[data-addrow-for="${CSS.escape(marker)}"] .task-input`;
  const inp = document.querySelector(sel);
  if (inp) inp.focus();
}

async function parsePasted(text, owner, module) {
  showToast("Parsing pasted text...");
  const r = await fetch("/api/parse", {method:"POST", headers:{"Content-Type":"application/json"}, body: JSON.stringify({
    text, defaults: {owner: owner || undefined, module: module || undefined},
  })});
  const data = await r.json();
  if (data.error) { showToast("Parse failed: " + data.error); return; }
  await loadTasks();
  showToast(`Added ${data.results.length} task(s) from paste`);
}

// ---------- Row interactions ----------
function attachRowHandlers() {
  document.querySelectorAll("#task-rows tr.data-row").forEach(tr => {
    const id = parseInt(tr.dataset.id);

    {
      const hit = tr.querySelector(".done-hit");
      const cb = hit.querySelector(".done-chk");
      // Native checkbox click already toggles `checked` once — don't flip it again here
      // (that double-toggle was the bug where clicking the box appeared to do nothing).
      hit.addEventListener("click", (e) => {
        e.stopPropagation();
        if (e.target !== cb) cb.click(); // clicking the padding around the box still toggles it
      });
      cb.addEventListener("click", (e) => e.stopPropagation());
      cb.addEventListener("change", () => toggleDone(id, tr, cb.checked));
    }

    tr.addEventListener("focus", () => { activeRowId = id; tr.classList.add("active-row"); });
    tr.addEventListener("click", (e) => {
      activeRowId = id;
      if (e.ctrlKey || e.metaKey) { toggleSelect(id, tr); return; }
    });

    tr.querySelectorAll(".editable-cell").forEach(td => {
      td.addEventListener("click", (e) => {
        if (e.ctrlKey || e.metaKey) return;
        if (td.querySelector("input,select")) return;
        startEdit(td, id);
      });
    });

    tr.querySelector(".rowmenu-btn").addEventListener("click", (e) => { e.stopPropagation(); openRowMenu(e.currentTarget, id); });

    tr.addEventListener("dragstart", () => { dragRowId = id; tr.style.opacity = "0.4"; });
    tr.addEventListener("dragend", () => { tr.style.opacity = "1"; });
    tr.addEventListener("dragover", (e) => e.preventDefault());
    tr.addEventListener("drop", async (e) => {
      e.preventDefault();
      if (dragRowId === null || dragRowId === id) return;
      const dragged = taskById(dragRowId), target = taskById(id);
      const field = groupFieldForViewBy();
      if (field && dragged[field] !== target[field]) {
        await patchTask(dragRowId, { [field]: target[field] || (field === "module" ? "" : target[field]) });
        showToast(`Moved to ${target[field] || "(no use case)"}`);
        await loadTasks();
        return;
      }
      const ids = [...document.querySelectorAll("#task-rows tr.data-row")].map(r => parseInt(r.dataset.id));
      const from = ids.indexOf(dragRowId), to = ids.indexOf(id);
      ids.splice(to, 0, ids.splice(from, 1)[0]);
      await fetch("/api/reorder", {method:"POST", headers:{"Content-Type":"application/json"}, body: JSON.stringify({ids})});
      sortCol = null;
      await loadTasks();
    });
  });
}

function attachCellIconHandlers() {
  document.querySelectorAll("[data-priority-for]").forEach(el => {
    el.addEventListener("click", async (e) => {
      e.stopPropagation();
      const id = parseInt(el.dataset.priorityFor);
      const t = taskById(id);
      await patchTask(id, { priority: t.priority === "High" ? "Normal" : "High" });
      renderBoard();
    });
  });
  document.querySelectorAll("[data-review-for]").forEach(el => {
    el.addEventListener("click", async (e) => {
      e.stopPropagation();
      const id = parseInt(el.dataset.reviewFor);
      const t = taskById(id);
      if (!t.review_status) {
        // one click, no form: instantly assign to Akshit (the only reviewer)
        await patchTask(id, { review_status: "Review pending", reviewer: CURRENT_USER });
        renderBoard(); renderSummary();
        showToast("Assigned to Akshit for review", () => { patchTask(id, {clear_review:true}).then(()=>{renderBoard();renderSummary();}); });
      } else {
        openReviewPopover(el, id);
      }
    });
  });
  document.querySelectorAll(".notes-inline").forEach(el => attachNotesInline(el));
  document.querySelectorAll("[data-notes-more]").forEach(el => {
    el.addEventListener("click", (e) => { e.stopPropagation(); openTaskNotesPanel(parseInt(el.dataset.notesMore)); });
  });
}

function autoGrow(el) { el.style.height = "auto"; el.style.height = Math.min(el.scrollHeight, 120) + "px"; }

function attachNotesInline(el) {
  const id = parseInt(el.dataset.notesFor);
  el.addEventListener("click", (e) => e.stopPropagation());
  el.addEventListener("focus", () => {
    el.dataset.preview = el.value;
    el.value = "";
    el.rows = 3;
    autoGrow(el);
  });
  el.addEventListener("input", () => autoGrow(el));
  el.addEventListener("keydown", async (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      if (el.value.trim()) {
        const text = el.value.trim();
        await fetch(`/api/tasks/${id}/notes`, {method:"POST", headers:{"Content-Type":"application/json"}, body: JSON.stringify({author: CURRENT_USER, text})});
        NOTE_COUNTS = await (await fetch("/api/note_counts")).json();
        // show what was just written instead of clearing to blank — blur() below
        // won't re-submit since value now matches the (updated) preview it compares against.
        el.dataset.preview = text;
        el.value = text;
        showToast("Note added");
        el.blur();
      }
    }
    if (e.key === "Escape") { el.value = el.dataset.preview || ""; el.blur(); }
  });
  el.addEventListener("blur", async () => {
    if (el.value.trim() && el.value.trim() !== (el.dataset.preview || "")) {
      await fetch(`/api/tasks/${id}/notes`, {method:"POST", headers:{"Content-Type":"application/json"}, body: JSON.stringify({author: CURRENT_USER, text: el.value.trim()})});
      NOTE_COUNTS = await (await fetch("/api/note_counts")).json();
    }
    el.rows = 1;
    el.style.height = "";
    renderBoard();
  });
}

async function toggleDone(id, tr, checked) {
  const newStatus = checked ? "Done" : "Open";
  await patchTask(id, {status: newStatus});
  tr.classList.toggle("done-row", checked);
  renderSummary();
  if (checked) {
    const undo = () => { patchTask(id, {status:"Open"}).then(()=>{ tr.classList.remove("done-row"); renderSummary(); }); };
    showToast("Marked done", undo);
  }
}
function deleteWithUndo(id) {
  const t = taskById(id);
  fetch(`/api/tasks/${id}`, {method:"DELETE"}).then(() => {
    TASKS = TASKS.filter(x => x.id !== id);
    renderBoard();
    const undo = async () => {
      await fetch("/api/tasks", {method:"POST", headers:{"Content-Type":"application/json"}, body: JSON.stringify({
        task:t.task, owner:t.owner, module:t.module, track:t.track, due:t.due, priority:t.priority})});
      await loadTasks();
    };
    showToast("Deleted", undo);
  });
}

function toggleSelect(id, tr) {
  if (selected.has(id)) { selected.delete(id); tr.classList.remove("selected"); }
  else { selected.add(id); tr.classList.add("selected"); }
  renderBulkbar();
}
function renderBulkbar() {
  const bar = document.getElementById("bulkbar");
  bar.style.display = selected.size ? "flex" : "none";
  document.getElementById("bulk-count").textContent = `${selected.size} selected`;
}

function startEdit(td, id) {
  const field = td.dataset.field;
  const t = taskById(id);
  let input;
  if (field === "due") { input = document.createElement("input"); input.type = "date"; input.value = t.due; }
  else if (field === "priority") {
    input = document.createElement("select");
    ["Normal","High"].forEach(p => { const o=document.createElement("option"); o.value=p; o.textContent=p; if (p===t.priority) o.selected=true; input.appendChild(o); });
  } else if (field === "execution_state") {
    input = document.createElement("select");
    ["","Not started","In progress","Blocked"].forEach(p => { const o=document.createElement("option"); o.value=p; o.textContent=p||"(none)"; if (p===(t.execution_state||"")) o.selected=true; input.appendChild(o); });
  } else if (field === "module") { input = document.createElement("input"); input.value = t.module || ""; }
  else if (field === "task") { input = document.createElement("input"); input.value = t.task; }
  else { input = document.createElement("input"); input.value = t[field] || ""; }

  const original = td.innerHTML;
  td.innerHTML = ""; td.appendChild(input); input.focus();
  if (input.select) input.select();

  let done = false;
  const commit = async () => {
    if (done) return; done = true;
    const val = input.value;
    try { await patchTask(id, {[field]: val}); renderBoard(); const flash = showToast("Saved"); setTimeout(flash, 1000); }
    catch (err) { renderBoard(); }
  };
  const cancel = () => { if (!done) { done = true; td.innerHTML = original; } };
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter") commit();
    if (e.key === "Escape") cancel();
    if (e.key === "Tab") { commit(); }
  });
  input.addEventListener("blur", commit);
}

// ---------- Row "more" menu ----------
function openRowMenu(anchor, id) {
  closePopovers();
  const t = taskById(id);
  const pop = document.createElement("div");
  pop.className = "popover";
  pop.innerHTML = `
    <div class="pop-item" data-act="edit">Edit task</div>
    <div class="pop-item" data-act="dup">Duplicate</div>
    <div class="pop-item" data-act="note">Add note</div>
    <div class="pop-item" data-act="review">Mark for review</div>
    <div class="pop-item" data-act="move">Move to another use case</div>
    <div class="pop-item" data-act="collab">Edit collaborators</div>
    <div class="pop-item" data-act="link">Copy task link</div>
    <hr>
    <div class="pop-item danger" data-act="delete">Delete</div>
  `;
  document.body.appendChild(pop);
  const r = anchor.getBoundingClientRect();
  pop.style.top = (r.bottom + window.scrollY) + "px";
  pop.style.left = (r.left - 160 + window.scrollX) + "px";

  pop.querySelector('[data-act="edit"]').onclick = () => { closePopovers(); const td = document.querySelector(`tr[data-id="${id}"] [data-field="task"]`); if (td) startEdit(td, id); };
  pop.querySelector('[data-act="dup"]').onclick = async () => {
    closePopovers();
    await fetch("/api/tasks", {method:"POST", headers:{"Content-Type":"application/json"}, body: JSON.stringify({
      task: t.task, owner: t.owner, collaborators: t.collaborators, module: t.module, track: t.track, priority: t.priority})});
    await loadTasks();
    showToast("Duplicated");
  };
  pop.querySelector('[data-act="note"]').onclick = () => { closePopovers(); openTaskNotesPanel(id); };
  pop.querySelector('[data-act="review"]').onclick = async () => {
    closePopovers();
    if (!t.review_status) {
      await patchTask(id, { review_status: "Review pending", reviewer: CURRENT_USER });
      renderBoard(); renderSummary(); showToast("Assigned to Akshit for review");
    } else { openReviewPopover(anchor, id); }
  };
  pop.querySelector('[data-act="move"]').onclick = () => {
    pop.innerHTML = `<label>Move to use case</label><input class="move-module" list="module-list" value="${esc(t.module||'')}"><div class="actions"><button class="secondary small" data-act="cancel">Cancel</button><button class="small" data-act="go">Move</button></div>`;
    pop.querySelector('[data-act="cancel"]').onclick = closePopovers;
    pop.querySelector('[data-act="go"]').onclick = async () => { await patchTask(id, {module: pop.querySelector(".move-module").value}); closePopovers(); renderBoard(); showToast("Moved"); };
  };
  pop.querySelector('[data-act="collab"]').onclick = () => {
    pop.innerHTML = `<label>Collaborators (comma-separated, in addition to owner ${esc(t.owner)})</label><input class="collab-input" list="owner-list" value="${esc(t.collaborators||'')}" placeholder="e.g. Abhishek, Hriday"><div class="actions"><button class="secondary small" data-act="cancel">Cancel</button><button class="small" data-act="go">Save</button></div>`;
    pop.querySelector('[data-act="cancel"]').onclick = closePopovers;
    pop.querySelector('[data-act="go"]').onclick = async () => { await patchTask(id, {collaborators: pop.querySelector(".collab-input").value}); closePopovers(); renderBoard(); showToast("Collaborators updated"); };
  };
  pop.querySelector('[data-act="link"]').onclick = () => {
    const link = `${location.origin}/#task-${id}`;
    navigator.clipboard?.writeText(link);
    closePopovers(); showToast("Link copied (local dashboard only)");
  };
  pop.querySelector('[data-act="delete"]').onclick = () => {
    pop.innerHTML = `<div style="font-size:12px;margin-bottom:8px;">Delete &ldquo;${esc(t.task.slice(0,60))}${t.task.length>60?'…':''}&rdquo;?</div>
      <div class="actions"><button class="secondary small" data-act="cancel">Cancel</button><button class="small" style="background:var(--red);" data-act="go">Delete</button></div>`;
    pop.querySelector('[data-act="cancel"]').onclick = closePopovers;
    pop.querySelector('[data-act="go"]').onclick = () => { closePopovers(); deleteWithUndo(id); };
  };
}

// ---------- Review popover (resolve an existing review only — assignment is a single click, no form) ----------
function openReviewPopover(anchor, id) {
  closePopovers();
  const t = taskById(id);
  const pop = document.createElement("div");
  pop.className = "popover";
  {
    pop.innerHTML = `
      <div style="font-weight:700;font-size:12px;">Review: ${esc(t.review_status)}</div>
      <div class="added-text" style="margin:4px 0;">Reviewer: ${esc(t.reviewer)} &middot; ${esc(t.review_type||'')} ${t.review_due?('&middot; due '+esc(t.review_due)):''}</div>
      ${t.review_comment ? `<div style="font-size:12px;margin-bottom:6px;">"${esc(t.review_comment)}"</div>` : ""}
      <label>New comment (optional)</label><textarea class="rv-comment2" rows="2"></textarea>
      <div class="actions">
        <button class="secondary small" data-act="clear">Clear review</button>
        <button class="secondary small" data-act="changes">Request changes</button>
        <button class="small" data-act="reviewed">Mark reviewed</button>
      </div>`;
    pop.querySelector('[data-act="clear"]').onclick = async () => { await patchTask(id, {clear_review:true}); closePopovers(); renderBoard(); showToast("Review cleared"); };
    pop.querySelector('[data-act="changes"]').onclick = async () => { await patchTask(id, {review_status:"Changes requested", review_comment: pop.querySelector(".rv-comment2").value || t.review_comment}); closePopovers(); renderBoard(); showToast("Changes requested"); };
    pop.querySelector('[data-act="reviewed"]').onclick = async () => { await patchTask(id, {review_status:"Reviewed", review_comment: pop.querySelector(".rv-comment2").value || t.review_comment}); closePopovers(); renderBoard(); showToast("Marked reviewed"); };
  }
  document.body.appendChild(pop);
  const r = anchor.getBoundingClientRect();
  pop.style.top = (r.bottom + window.scrollY) + "px";
  pop.style.left = (r.left + window.scrollX) + "px";
}

// ---------- Notes: persistent right-docked panel (stays open while you browse the table) ----------
let notesPanelTaskId = null;

async function openTaskNotesPanel(id) {
  notesPanelTaskId = id;
  const panel = document.getElementById("notes-panel");
  panel.style.display = "flex";
  document.querySelector("main").style.marginRight = "320px";
  await refreshNotesPanel();
}
function closeNotesPanel() {
  notesPanelTaskId = null;
  document.getElementById("notes-panel").style.display = "none";
  document.querySelector("main").style.marginRight = "0";
}
async function refreshNotesPanel() {
  if (notesPanelTaskId === null) return;
  const id = notesPanelTaskId;
  const t = taskById(id);
  const panel = document.getElementById("notes-panel");
  if (!t) { closeNotesPanel(); return; }
  const notes = await (await fetch(`/api/tasks/${id}/notes`)).json();
  panel.innerHTML = `
    <div class="np-header">
      <div>
        <div class="np-task">${esc(t.task)}</div>
        <div class="added-text">${esc(t.owner)} &middot; ${esc(t.module||t.track)} &middot; due ${fmtDateShort(t.due)}</div>
      </div>
      <span class="np-close" id="np-close">&times;</span>
    </div>
    <div class="note-list">${notes.length ? notes.map(n => `
      <div class="note-item" data-note-id="${n.id}">
        <div class="meta"><span>${esc(n.author)} ${n.pinned?'&#128204;':''}</span><span>${fmtDateShort(n.created_at.slice(0,10))}</span></div>
        <div>${esc(n.text)}</div>
        <div class="actions">
          <span data-act="pin">${n.pinned?'Unpin':'Pin'}</span>
          ${n.author===CURRENT_USER?'<span data-act="del">Delete</span>':''}
        </div>
      </div>`).join("") : `<div class="added-text">No notes yet — add the first one below.</div>`}</div>
    <input class="note-input" placeholder="Add a note... (Enter to add)">`;
  panel.querySelector("#np-close").onclick = closeNotesPanel;
  panel.querySelectorAll('[data-act="pin"]').forEach(el => el.onclick = async () => { await fetch(`/api/notes/${el.closest('.note-item').dataset.noteId}/pin`, {method:"POST"}); await refreshNotesPanel(); });
  panel.querySelectorAll('[data-act="del"]').forEach(el => el.onclick = async () => { await fetch(`/api/notes/${el.closest('.note-item').dataset.noteId}?author=${encodeURIComponent(CURRENT_USER)}`, {method:"DELETE"}); await refreshNotesPanel(); loadTasks(); });
  const input = panel.querySelector(".note-input");
  input.addEventListener("keydown", async (e) => {
    if (e.key === "Enter" && input.value.trim()) {
      await fetch(`/api/tasks/${id}/notes`, {method:"POST", headers:{"Content-Type":"application/json"}, body: JSON.stringify({author: CURRENT_USER, text: input.value.trim()})});
      input.value = ""; await refreshNotesPanel(); loadTasks();
    }
  });
}

// ---------- Filter panels ----------
function openFilterPanel(anchor, col) {
  const panel = document.createElement("div");
  panel.className = "filter-panel";
  if (col === "due") {
    panel.innerHTML = ["all","overdue","today","week"].map(v =>
      `<label><input type="radio" name="duef" value="${v}" ${filters.due===v?'checked':''}> ${v==='all'?'All':v==='overdue'?'Overdue':v==='today'?'Due today':'Due this week'}</label>`
    ).join("");
    panel.querySelectorAll("input").forEach(inp => inp.onchange = () => { filters.due = inp.value; markFilterIcon(); renderBoard(); });
  } else if (col === "status") {
    panel.innerHTML = ["all","Open","Done"].map(v =>
      `<label><input type="radio" name="statusf" value="${v}" ${filters.status===v?'checked':''}> ${v==='all'?'All':v}</label>`
    ).join("");
    panel.querySelectorAll("input").forEach(inp => inp.onchange = () => { filters.status = inp.value; markFilterIcon(); renderBoard(); });
  } else if (["owner","module","priority"].includes(col)) {
    const values = col === "owner" ? window.META.owners : col === "module" ? window.META.modules : window.META.priorities;
    const activeSet = filters[col];
    panel.innerHTML = `
      <button class="fp-clear-btn" data-act="clear-filter">Clear filter — show everyone</button>
      <input type="text" class="fp-search" placeholder="Type a name and press Enter...">
      <div class="fp-actions"><a data-act="sortasc">Sort A-Z</a><a data-act="sortdesc">Sort Z-A</a></div>
      ${col==="module" ? `<label class="fp-row"><input type="checkbox" class="fp-blank" ${filters.moduleBlank?'checked':''}> <span>(Blanks only)</span></label><hr>` : ""}
      <label class="fp-row fp-master"><input type="checkbox" class="fp-select-all"> <span>(Select all)</span></label>
      <div class="fp-list">${values.map(v => `<label class="fp-row" data-val="${esc(v)}"><input type="checkbox" value="${esc(v)}" ${(!activeSet || activeSet.has(v)) ? 'checked':''}> <span>${esc(v)}</span></label>`).join("")}</div>`;

    panel.querySelector('[data-act="clear-filter"]').onclick = () => {
      filters[col] = null;
      if (col === "module") filters.moduleBlank = false;
      markFilterIcon(); renderBoard(); closePopovers();
    };
    panel.querySelector('[data-act="sortasc"]').onclick = () => { sortCol=col; sortDir=1; renderBoard(); };
    panel.querySelector('[data-act="sortdesc"]').onclick = () => { sortCol=col; sortDir=-1; renderBoard(); };
    const blankCb = panel.querySelector(".fp-blank");
    if (blankCb) blankCb.onchange = () => { filters.moduleBlank = blankCb.checked; markFilterIcon(); renderBoard(); };

    function visibleRows() { return [...panel.querySelectorAll(".fp-list .fp-row")].filter(l => l.style.display !== "none"); }
    function syncSelectAll() {
      const rows = visibleRows();
      const allChecked = rows.length > 0 && rows.every(l => l.querySelector("input").checked);
      panel.querySelector(".fp-select-all").checked = allChecked;
    }
    function applyFromCheckboxes() {
      const checked = [...panel.querySelectorAll(".fp-list input[type=checkbox]")].filter(cb => cb.checked).map(cb => cb.value);
      filters[col] = checked.length === values.length ? null : new Set(checked);
      markFilterIcon(); renderBoard(); syncSelectAll();
    }
    panel.querySelectorAll('.fp-list input[type=checkbox]').forEach(cb => { cb.onchange = applyFromCheckboxes; });
    panel.querySelector(".fp-select-all").onchange = (e) => {
      // (Select all) only affects rows currently visible — same convention as Excel's filter search
      visibleRows().forEach(l => { l.querySelector("input").checked = e.target.checked; });
      applyFromCheckboxes();
    };
    syncSelectAll();

    let hlIndex = 0;
    function highlight() {
      visibleRows().forEach((l, i) => l.classList.toggle("fp-hl", i === hlIndex));
    }
    const searchInput = panel.querySelector(".fp-search");
    searchInput.addEventListener("input", (e) => {
      const q = e.target.value.toLowerCase();
      panel.querySelectorAll(".fp-list .fp-row").forEach(l => { l.style.display = l.dataset.val.toLowerCase().includes(q) ? "flex" : "none"; });
      hlIndex = 0;
      highlight();
      syncSelectAll();
    });
    searchInput.addEventListener("keydown", (e) => {
      const rows = visibleRows();
      if (e.key === "ArrowDown") { e.preventDefault(); hlIndex = Math.min(hlIndex + 1, rows.length - 1); highlight(); }
      else if (e.key === "ArrowUp") { e.preventDefault(); hlIndex = Math.max(hlIndex - 1, 0); highlight(); }
      else if (e.key === "Enter") {
        e.preventDefault();
        if (!rows.length) return;
        const target = rows[Math.max(0, Math.min(hlIndex, rows.length - 1))];
        const cb = target.querySelector("input");
        // typing a name + Enter narrows to exactly that one (replacing "everyone"); a second
        // name + Enter afterward ADDS to the existing pick, so you can build a multi-select.
        const already = filters[col] instanceof Set;
        const base = already ? new Set(filters[col]) : new Set();
        base.add(cb.value);
        filters[col] = base.size === values.length ? null : base;
        markFilterIcon(); renderBoard();
        searchInput.value = "";
        panel.querySelectorAll(".fp-list .fp-row").forEach(l => { l.style.display = "flex"; });
        panel.querySelectorAll(".fp-list input[type=checkbox]").forEach(box => { box.checked = !filters[col] || filters[col].has(box.value); });
        syncSelectAll();
        hlIndex = 0; highlight();
      }
    });
    highlight();
    searchInput.focus();
  } else {
    panel.innerHTML = `<div class="added-text">No filter for this column.</div>`;
  }
  // Append to body (not the <th>) — a table cell inside a horizontally-scrolling
  // wrapper gets its overflow-y forced to match overflow-x (a CSS spec quirk),
  // which was silently clipping most of this panel's height.
  document.body.appendChild(panel);
  const th = anchor.closest("th");
  const r = th.getBoundingClientRect();
  panel.style.top = (r.bottom + window.scrollY) + "px";
  panel.style.left = (r.left + window.scrollX) + "px";
}
function markFilterIcon() {
  document.querySelectorAll("th .filt").forEach(el => {
    const col = el.dataset.col;
    const active = col === "due" ? filters.due !== "all" : col === "status" ? filters.status !== "all" : col === "module" ? (filters.moduleBlank || !!filters.module) : !!filters[col];
    el.classList.toggle("on", active);
  });
  renderChips();
}

// ---------- Column manager ----------
document.getElementById("columns-btn").addEventListener("click", (e) => {
  e.stopPropagation();
  closePopovers();
  const pop = document.createElement("div");
  pop.className = "popover";
  pop.innerHTML = Object.keys(ALL_COLUMNS).map(key => `
    <label style="display:flex;"><input type="checkbox" data-col-toggle="${key}" ${columns.includes(key)?'checked':''} ${key==='task'?'disabled':''}> ${ALL_COLUMNS[key].label}</label>
  `).join("") + `<div class="added-text" style="margin-top:6px;">Drag column headers to reorder or resize.</div>
  <div class="pop-item" data-act="reset-cols" style="margin-top:4px;color:var(--accent);">Reset to default</div>`;
  document.body.appendChild(pop);
  const r = e.target.getBoundingClientRect();
  pop.style.top = (r.bottom + window.scrollY) + "px"; pop.style.left = (r.left + window.scrollX) + "px";
  pop.querySelectorAll("[data-col-toggle]").forEach(cb => {
    cb.onchange = () => {
      const key = cb.dataset.colToggle;
      if (cb.checked) { if (!columns.includes(key)) columns.push(key); }
      else columns = columns.filter(c => c !== key);
      saveColumnsForView(); buildHeader(); renderBoard();
    };
  });
  pop.querySelector('[data-act="reset-cols"]').onclick = () => {
    columns = DEFAULT_COLUMNS.slice();
    columnWidths = {};
    localStorage.removeItem("pmo_col_widths_v2");
    saveColumnsForView();
    closePopovers(); buildHeader(); renderBoard();
    showToast("Columns reset to default");
  };
});

// ---------- Saved views ----------
const BUILTIN_VIEWS = {
  "All tasks": { viewBy:"flat", thenBy:"none", filters:{owner:null,module:null,priority:null,due:"all",status:"all",moduleBlank:false}, quickFilter:null },
  "By use case": { viewBy:"module", thenBy:"none" },
  "By owner": { viewBy:"owner", thenBy:"none" },
  "My tasks": { viewBy:"flat", thenBy:"none", quickFilter:"myTasks" },
  "My reviews": { viewBy:"review", thenBy:"none", quickFilter:"myReviews" },
  "Overdue": { viewBy:"flat", thenBy:"none", quickFilter:"overdue" },
  "Due this week": { viewBy:"flat", thenBy:"none", quickFilter:"dueWeek" },
  "Recently added": { viewBy:"flat", thenBy:"none", sortCol:"added", sortDir:-1 },
  "Completed": { viewBy:"flat", thenBy:"none", quickFilter:"completed", hideCompleted:false },
};
function populateViewsSelect() {
  const sel = document.getElementById("views-select");
  const saved = JSON.parse(localStorage.getItem("pmo_saved_views") || "{}");
  sel.innerHTML = `<option value="">Saved views...</option>` +
    `<optgroup label="Built-in">` + Object.keys(BUILTIN_VIEWS).map(n=>`<option value="b:${esc(n)}">${esc(n)}</option>`).join("") + `</optgroup>` +
    (Object.keys(saved).length ? `<optgroup label="Custom">` + Object.keys(saved).map(n=>`<option value="c:${esc(n)}">${esc(n)}</option>`).join("") + `</optgroup>` : "");
}
function applyViewConfig(cfg) {
  viewBy = cfg.viewBy ?? "flat"; thenBy = cfg.thenBy ?? "none";
  filters = cfg.filters ?? { owner: null, module: null, priority: null, due: "all", status: "all", moduleBlank: false };
  quickFilter = cfg.quickFilter ?? null;
  sortCol = cfg.sortCol ?? null; sortDir = cfg.sortDir ?? 1;
  hideCompleted = cfg.hideCompleted ?? false;
  density = cfg.density ?? density;
  columns = cfg.columns ?? loadColumnsForView(viewBy);
  document.getElementById("view-by").value = viewBy;
  document.getElementById("then-by").value = thenBy;
  document.getElementById("density-btn").textContent = `Density: ${density === "compact" ? "Compact" : "Comfortable"}`;
  renderAll();
}
document.getElementById("views-select").addEventListener("change", (e) => {
  const val = e.target.value; if (!val) return;
  const [kind, name] = [val[0], val.slice(2)];
  if (kind === "b") applyViewConfig(BUILTIN_VIEWS[name]);
  else { const saved = JSON.parse(localStorage.getItem("pmo_saved_views") || "{}"); applyViewConfig(saved[name]); }
});
document.getElementById("save-view-btn").addEventListener("click", () => {
  const name = window.prompt("Name this view:");
  if (!name) return;
  const saved = JSON.parse(localStorage.getItem("pmo_saved_views") || "{}");
  saved[name] = { viewBy, thenBy, filters, quickFilter, sortCol, sortDir, hideCompleted, density, columns };
  localStorage.setItem("pmo_saved_views", JSON.stringify(saved));
  populateViewsSelect();
  showToast(`Saved view "${name}"`);
});

// ---------- Toolbar wiring ----------
document.getElementById("f-search").addEventListener("input", (e) => { searchQ = e.target.value.toLowerCase(); renderBoard(); });
document.getElementById("view-by").addEventListener("change", (e) => { viewBy = e.target.value; columns = loadColumnsForView(viewBy); renderAll(); });
document.getElementById("then-by").addEventListener("change", (e) => { thenBy = e.target.value; renderBoard(); });
document.getElementById("hide-completed-btn").addEventListener("click", (e) => { hideCompleted = !hideCompleted; e.target.classList.toggle("toggle-on", hideCompleted); renderBoard(); });
document.getElementById("density-btn").addEventListener("click", (e) => {
  density = density === "comfortable" ? "compact" : "comfortable";
  localStorage.setItem("pmo_density", density);
  e.target.textContent = `Density: ${density === "compact" ? "Compact" : "Comfortable"}`;
  renderBoard();
});
document.getElementById("filters-btn").addEventListener("click", () => {
  filters = { owner: null, module: null, priority: null, due: "all", status: "all", moduleBlank: false };
  markFilterIcon(); renderBoard();
});
document.getElementById("add-btn").addEventListener("click", () => {
  const inp = document.querySelector('.addrow[data-addrow-for="__top__"] .task-input');
  if (inp) { inp.scrollIntoView({block:"center"}); inp.focus(); }
});

document.getElementById("bulk-owner-apply").onclick = async () => {
  const owner = document.getElementById("bulk-owner").value; if (!owner) return;
  await fetch("/api/bulk", {method:"POST", headers:{"Content-Type":"application/json"}, body: JSON.stringify({ids:[...selected], fields:{owner}})});
  await loadTasks();
};
document.getElementById("bulk-status-apply").onclick = async () => {
  const status = document.getElementById("bulk-status").value;
  await fetch("/api/bulk", {method:"POST", headers:{"Content-Type":"application/json"}, body: JSON.stringify({ids:[...selected], fields:{status}})});
  await loadTasks();
};
document.getElementById("bulk-due-apply").onclick = async () => {
  const due = document.getElementById("bulk-due").value; if (!due) return;
  await fetch("/api/bulk", {method:"POST", headers:{"Content-Type":"application/json"}, body: JSON.stringify({ids:[...selected], fields:{due}})});
  await loadTasks();
};
document.getElementById("bulk-clear").onclick = () => { selected.clear(); renderBulkbar(); renderBoard(); };

document.querySelectorAll(".tab").forEach(tab => {
  tab.onclick = () => {
    document.querySelectorAll(".tab").forEach(t=>t.classList.remove("active"));
    tab.classList.add("active");
    document.getElementById("board-view").style.display = tab.dataset.tab === "board" ? "block" : "none";
    document.getElementById("gantt-view").style.display = tab.dataset.tab === "gantt" ? "block" : "none";
  };
});

// ---------- Keyboard shortcuts ----------
document.addEventListener("keydown", (e) => {
  if (e.target.matches("input,select,textarea")) {
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") { e.preventDefault(); document.getElementById("f-search").focus(); }
    return;
  }
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") { e.preventDefault(); document.getElementById("f-search").focus(); return; }
  const key = e.key.toLowerCase();
  if (key === "n") { e.preventDefault(); document.getElementById("add-btn").click(); }
  else if (activeRowId !== null) {
    const tr = document.querySelector(`tr[data-id="${activeRowId}"]`);
    if (!tr) return;
    if (key === "e") { e.preventDefault(); const td = tr.querySelector('[data-field="task"]'); if (td) startEdit(td, activeRowId); }
    else if (key === "r") { e.preventDefault(); const el = tr.querySelector("[data-review-for]"); if (el) openReviewPopover(el, activeRowId); }
    else if (key === "m") { e.preventDefault(); const el = tr.querySelector(".notes-inline"); if (el) el.focus(); }
    else if (key === "delete" || key === "backspace") { e.preventDefault(); openRowMenu(tr.querySelector(".rowmenu-btn"), activeRowId); tr.querySelector('[data-act="delete"]')?.click(); }
    else if (key === "arrowdown" || key === "arrowup") {
      e.preventDefault();
      const rows = [...document.querySelectorAll("#task-rows tr.data-row")];
      const idx = rows.findIndex(r => parseInt(r.dataset.id) === activeRowId);
      const next = rows[idx + (key === "arrowdown" ? 1 : -1)];
      if (next) { next.focus(); activeRowId = parseInt(next.dataset.id); renderBoard(); }
    }
  }
});

document.getElementById("density-btn").textContent = `Density: ${density === "compact" ? "Compact" : "Comfortable"}`;
populateViewsSelect();
loadMeta().then(loadTasks);
