"use strict";

// A session that ends (signed out elsewhere, expired) sends every API call back to the sign-in page.
const nativeFetch = window.fetch.bind(window);
window.fetch = async (...args) => {
  const response = await nativeFetch(...args);
  if (response.status === 401 || response.status === 403) location.replace("/login");
  return response;
};

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

/** Creates an element; children are nodes or strings (always inserted as text). */
function h(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value === undefined || value === null || value === false) continue;
    if (key === "class") node.className = value;
    else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else node.setAttribute(key, value === true ? "" : value);
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child instanceof Node ? child : String(child));
  }
  return node;
}

/** An icon from the sprite in index.html. */
function icon(name, cls = "") {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("class", `i ${cls}`.trim());
  const use = document.createElementNS("http://www.w3.org/2000/svg", "use");
  use.setAttribute("href", `#i-${name}`);
  svg.append(use);
  return svg;
}

const compact = (value) => JSON.stringify(value);
const keyText = (key) => key ? Object.entries(key).map(([k, v]) => `${k}=${v}`).join(", ") : "";
const fmtBytes = (n) => n < 1024 ? `${n} B` : n < 1048576 ? `${(n / 1024).toFixed(1)} KB` : `${(n / 1048576).toFixed(1)} MB`;
const fmtTime = (ms) => ms ? new Date(ms).toLocaleTimeString([], { hour12: false }) + "." + String(ms % 1000).padStart(3, "0") : "";
const reduceMotion = matchMedia("(prefers-reduced-motion: reduce)");

/** A seq with its zero padding faded, so "000…0014.0000000000" reads as 14. The text is unchanged. */
function seqNodes(seq) {
  if (!seq) return [];
  const [, lead, sig, trail] = /^([0.]*)(.*?)(\.0+)?$/.exec(seq);
  const nodes = [h("span", { class: "seq-zero" }, lead), h("span", { class: "seq-sig" }, sig)];
  if (trail) nodes.push(h("span", { class: "seq-zero" }, trail));
  return nodes;
}

/** Restarts a CSS animation class on an element, at most once per run. */
function replay(node, cls) {
  if (!node || reduceMotion.matches || node.classList.contains(cls)) return;
  node.classList.add(cls);
  node.addEventListener("animationend", () => node.classList.remove(cls), { once: true });
}

function toast(text) {
  const node = $("#toast");
  node.replaceChildren(icon("check"), text);
  node.classList.add("show");
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => node.classList.remove("show"), 1800);
}

// The sticky table headers sit just below the header, whatever its height.
new ResizeObserver(([entry]) => {
  document.documentElement.style.setProperty("--header-h", `${entry.target.offsetHeight}px`);
}).observe($("#top"));

// ---------------------------------------------------------------------------------------- theme

const darkQuery = matchMedia("(prefers-color-scheme: dark)");
const THEMES = ["system", "light", "dark"];
const themePref = () => document.documentElement.dataset.themePref || "system";
const resolveTheme = (pref) => pref === "dark" || (pref === "system" && darkQuery.matches) ? "dark" : "light";

const THEME_ICONS = { system: "monitor", light: "sun", dark: "moon" };
const THEME_NAMES = { system: "Theme: match system", light: "Theme: light", dark: "Theme: dark" };

/** The theme button shows the current choice; clicking it moves to the next. */
function syncThemeSwitch() {
  const button = $("#theme-toggle");
  button.replaceChildren(icon(THEME_ICONS[themePref()]));
  button.title = `${THEME_NAMES[themePref()]} (T to change)`;
}

/** Switches theme; with view transitions the new theme spreads in a circle from `origin`. */
function setTheme(pref, origin) {
  try { localStorage.setItem("theme", pref); } catch { /* private mode: not remembered */ }
  const root = document.documentElement;
  const next = resolveTheme(pref);
  const apply = () => { root.dataset.themePref = pref; root.dataset.theme = next; syncThemeSwitch(); };
  if (next === root.dataset.theme || !document.startViewTransition || reduceMotion.matches) { apply(); return; }
  const { x, y } = origin;
  const radius = Math.hypot(Math.max(x, innerWidth - x), Math.max(y, innerHeight - y));
  document.startViewTransition(apply).ready.then(() => root.animate(
    { clipPath: [`circle(0px at ${x}px ${y}px)`, `circle(${radius}px at ${x}px ${y}px)`] },
    { duration: 600, easing: "cubic-bezier(.4, 0, .2, 1)", pseudoElement: "::view-transition-new(root)" }));
}

const centre = (node) => { const r = node.getBoundingClientRect(); return { x: r.left + r.width / 2, y: r.top + r.height / 2 }; };
const nextTheme = () => setTheme(THEMES[(THEMES.indexOf(themePref()) + 1) % THEMES.length], centre($("#theme-toggle")));
$("#theme-toggle").addEventListener("click", nextTheme);
darkQuery.addEventListener("change", () => { if (themePref() === "system") setTheme("system", centre($("#theme-toggle"))); });
syncThemeSwitch();

// ---------------------------------------------------------------------------------------- user

fetch("/api/auth/me").then((r) => r.ok && r.json()).then((user) => {
  if (!user) return;
  $("#user-name").textContent = user.username;
  $("#user-avatar").textContent = user.username.slice(0, 1);
}).catch(() => {});

$("#sign-out").addEventListener("click", async () => {
  try { await nativeFetch("/api/auth/logout", { method: "POST" }); } finally { location.replace("/login"); }
});

// ---------------------------------------------------------------------------------------- tabs

const tabLoaders = {};

function moveTabIndicator() {
  const active = $("button.tab.active");
  const indicator = $(".tab-indicator");
  indicator.style.width = `${active.offsetWidth}px`;
  indicator.style.transform = `translateX(${active.offsetLeft}px)`;
}

function showTab(name) {
  $$("button.tab").forEach((b) => b.classList.toggle("active", b.dataset.tab === name));
  $$(".tab-panel").forEach((p) => p.classList.toggle("active", p.id === `tab-${name}`));
  moveTabIndicator();
  history.replaceState(null, "", name === "live" ? location.pathname : `#${name}`);
  tabLoaders[name]?.();
}

for (const button of $$("button.tab")) button.addEventListener("click", () => showTab(button.dataset.tab));
addEventListener("resize", moveTabIndicator);
document.fonts?.ready.then(moveTabIndicator);
moveTabIndicator();

// ---------------------------------------------------------------------------------- live events

const MAX_EVENTS = 5000;   // kept in memory, for filtering
const MAX_ROWS = 400;      // rendered
const KEEP_CHARS = 8192;   // events larger than this are not kept whole: the modal fetches them
const FILTER_CHARS = 2048; // the text filter searches the start of each event
const DRAW_INTERVAL = 100; // ms between table updates while events keep arriving

const live = {
  events: [],
  committed: "",           // the latest published seq
  committedAt: 0,          // when it was published (ms)
  catchingUp: true,        // replaying the backlog: render once at the end
  paused: false,
  missed: 0,               // matching events that arrived while paused
  counts: { c: 0, u: 0, d: 0, r: 0, control: 0 },
  tables: new Set(),
  pendingRows: [],         // rendered rows not yet covered by a published offset
  perSecond: new Map(),    // epoch second → changes captured, for the throughput chart
};

const pick = (row, keys) => Object.fromEntries(keys.map((k) => [k, row[k]]));

/**
 * The row images of a record-format event. Current events hold both in `record`
 * (`{"before": …, "after": …}`, `record_images: before_and_after`; a delete's `after` is the
 * deleted row's key); events written before that change, still in the lake, have the row in
 * `record` and the previous one in `before`.
 */
function images(e) {
  const r = e.record;
  if (r && typeof r === "object" && Object.keys(r).length === 2 && "before" in r && "after" in r) {
    return { before: r.before, after: r.after };
  }
  return { before: e.before ?? null, after: e.metadata?.op === "d" ? null : r };
}

/**
 * Turns one line of live.ndjson into what the feed shows. `data` is the JSON shown in the row:
 * the row image for inserts and snapshot reads, only the changed columns for updates, the
 * deleted row for deletes, and the whole event for everything else.
 */
function describe(e) {
  if (e.metadata && "record" in e) {
    const m = e.metadata;
    const { before, after } = images(e);
    let data = after;
    if (m.op === "u" && before && after) {
      const changed = Object.keys(after).filter((k) => compact(after[k]) !== compact(before[k]));
      data = { before: pick(before, changed), after: pick(after, changed) };
    } else if (m.op === "d") {
      data = before ?? m.key;
    }
    return {
      kind: m.op, label: { c: "insert", u: "update", d: "delete", r: "snapshot" }[m.op] ?? m.op,
      table: `${m.db}.${m.table}`, key: keyText(m.key), time: m.captured_ts_ms ?? m.commit_ts_ms, seq: m.seq, data,
    };
  }
  const time = e.timestamp ? Date.parse(e.timestamp) : undefined;
  if (["schema_change", "truncate", "snapshot_completed", "snapshot_held"].includes(e.type)) {
    return { kind: "control", label: e.type.replace("_", " "), table: e.table ? `${e.db}.${e.table}` : e.db, time, seq: e.seq, data: e };
  }
  if (e.schema_version && Array.isArray(e.columns)) {
    return { kind: "schema", label: "schema", table: `${e.db}.${e.table}`, time, data: e };
  }
  if (e.change_seq !== undefined && e.connector) {
    return { kind: "offset", label: "offset", table: e.connector, time: e.committed_at ? Date.parse(e.committed_at) : time, data: e };
  }
  return { kind: "other", label: "event", table: "", time, data: e };
}

// Strings (with a following colon for keys), literals and numbers in JSON text.
const JSON_TOKEN = /("(?:[^"\\]|\\.)*")(\s*:)?|\b(true|false|null)\b|-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?/g;

/** Syntax-highlighted JSON as nodes. `spaced` puts a space after each comma and colon of compact JSON. */
function jsonNodes(text, spaced = false) {
  const nodes = [];
  const punct = (s) => spaced ? s.replace(/[,:]/g, "$& ") : s;
  let last = 0;
  for (const m of text.matchAll(JSON_TOKEN)) {
    if (m.index > last) nodes.push(punct(text.slice(last, m.index)));
    const cls = m[1] ? (m[2] ? "j-key" : "j-str") : m[3] ? "j-lit" : "j-num";
    nodes.push(h("span", { class: cls }, m[1] ?? m[0]));
    if (m[2]) nodes.push(punct(m[2]));
    last = m.index + m[0].length;
  }
  nodes.push(punct(text.slice(last)));
  return nodes;
}

/** One line of live.ndjson, as received. */
function onEvent(raw) {
  const e = JSON.parse(raw);
  const { data, ...d } = describe(e);
  // Keep only what the feed needs. Rows with large JSON, TEXT or BLOB columns would otherwise
  // hold megabytes each, MAX_EVENTS times over.
  const item = {
    d,
    preview: compact(data).slice(0, 600),   // the row is cut off with an ellipsis anyway
    raw: raw.length <= KEEP_CHARS ? raw : null,
    size: raw.length,
    text: raw.slice(0, FILTER_CHARS).toLowerCase(),
  };
  live.events.push(item);
  // Trimmed in steps: removing from the front of a large array on every event is costly.
  if (live.events.length > MAX_EVENTS * 1.2) live.events.splice(0, live.events.length - MAX_EVENTS);
  if (d.kind in live.counts) {
    live.counts[d.kind]++;
    // The backlog counts too, by capture time, so the chart shows the last minute straight away.
    const t = live.catchingUp ? d.time : Date.now();
    if (t > Date.now() - 61000) {
      const second = Math.floor(t / 1000);
      live.perSecond.set(second, (live.perSecond.get(second) ?? 0) + 1);
    }
  }
  if (d.kind === "offset" && e.seq > live.committed) setCommitted(e);
  if (d.table && d.kind !== "offset" && !live.tables.has(d.table)) {
    live.tables.add(d.table);
    const select = $("#f-table");
    const options = [...live.tables].sort();
    const current = select.value;
    select.replaceChildren(h("option", { value: "" }, "All tables"), ...options.map((t) => h("option", { value: t }, t)));
    select.value = current;
  }
  if (live.catchingUp) return;
  if (live.paused) {
    if (matches(item)) { live.missed++; updatePauseButton(); }
    return;
  }
  if (matches(item)) queueRow(item);
  else scheduleDraw();   // the counters still change
}

// New rows are drawn in batches, at most every DRAW_INTERVAL ms and only on animation frames,
// so a fast stream costs one table update per batch, not one per event. When more than a
// screenful arrives between two updates, only the newest MAX_ROWS are drawn.
const draw = { queue: [], skipped: 0, scheduled: false, last: 0 };

function queueRow(item) {
  draw.queue.push(item);
  if (draw.queue.length > MAX_ROWS * 2) {
    draw.skipped += draw.queue.length - MAX_ROWS;
    draw.queue.splice(0, draw.queue.length - MAX_ROWS);
  }
  scheduleDraw();
}

function scheduleDraw() {
  if (draw.scheduled) return;
  draw.scheduled = true;
  const wait = Math.max(0, DRAW_INTERVAL - (performance.now() - draw.last));
  setTimeout(() => requestAnimationFrame(drawQueued), wait);
}

function drawQueued() {
  draw.scheduled = false;
  draw.last = performance.now();
  let items = draw.queue;
  draw.queue = [];
  if (items.length > MAX_ROWS) {
    draw.skipped += items.length - MAX_ROWS;
    items = items.slice(-MAX_ROWS);
  }
  if (items.length) {
    // Animating hundreds of new rows at once costs more than it shows.
    const fresh = items.length <= 30;
    const fragment = document.createDocumentFragment();
    for (let i = items.length - 1; i >= 0; i--) fragment.append(row(items[i], fresh));
    const feed = $("#feed");
    feed.prepend(fragment);
    while (feed.childElementCount > MAX_ROWS) feed.lastElementChild.remove();
    $("#feed-empty").hidden = true;
    if (live.pendingRows.length > MAX_ROWS * 2) {
      live.pendingRows = live.pendingRows.filter(({ badge }) => badge.isConnected);
    }
  }
  if (draw.skipped) {
    showStreamNotice(`Fast stream: showing the newest rows (${draw.skipped.toLocaleString()} not drawn)`);
    draw.skipped = 0;
  }
  updateCounts();
}

function showStreamNotice(text) {
  const notice = $("#stream-notice");
  notice.textContent = text;
  notice.hidden = false;
  clearTimeout(showStreamNotice.timer);
  showStreamNotice.timer = setTimeout(() => { notice.hidden = true; }, 3000);
}

function setCommitted(offset) {
  const advanced = live.committed !== "";
  live.committed = offset.seq;
  live.committedAt = Date.parse(offset.committed_at) || Date.now();
  $("#offset-seq").replaceChildren(...seqNodes(offset.seq));
  $("#offset-detail").textContent =
    `change ${offset.change_seq} · ${offset.file}:${offset.pos} · ${new Date(offset.committed_at).toLocaleTimeString([], { hour12: false })}` +
    (offset.snapshot_in_progress ? " · snapshot in progress" : "");
  live.pendingRows = live.pendingRows.filter(({ seq, badge }) => {
    if (seq > live.committed) return true;
    setLakeBadge(badge, true);
    replay(badge, "pop");
    return false;
  });
  if (advanced && !live.catchingUp) replay($(".card.commit"), "flash");
  tick();
}

function setLakeBadge(badge, landed) {
  badge.className = `badge ${landed ? "landed" : "pending"}`;
  badge.textContent = landed ? "in lake" : "batching";
}

/** Counts up (or down) to `target`, and gives the card a little bump when it grows. */
function setNumber(node, target) {
  const state = node.tween ??= { shown: 0, target: 0, frame: 0 };
  if (state.target === target) return;
  if (target > state.target) replay(node.closest(".card"), "bump");
  state.target = target;
  cancelAnimationFrame(state.frame);
  const from = state.shown;
  const start = performance.now();
  const duration = reduceMotion.matches ? 0 : 600;
  const step = (now) => {
    const p = duration ? Math.min(1, (now - start) / duration) : 1;
    state.shown = Math.round(from + (target - from) * (1 - (1 - p) ** 3));
    node.textContent = state.shown.toLocaleString();
    if (p < 1) state.frame = requestAnimationFrame(step);
  };
  state.frame = requestAnimationFrame(step);
}

function updateCounts() {
  for (const [kind, n] of Object.entries(live.counts)) setNumber($(`#n-${kind}`), n);
}

// Once a second: the throughput chart, how long ago the lake was last committed to, and how
// many captured changes are still waiting for a commit.
const SPARK_BARS = 60;
const spark = $("#spark");
const sparkBars = Array.from({ length: SPARK_BARS }, (_, i) => {
  const bar = document.createElementNS("http://www.w3.org/2000/svg", "rect");
  bar.setAttribute("x", i * (300 / SPARK_BARS) + 0.6);
  bar.setAttribute("width", 300 / SPARK_BARS - 1.2);
  bar.setAttribute("rx", 1.2);
  spark.append(bar);
  return bar;
});

function tick() {
  const now = Math.floor(Date.now() / 1000);
  for (const second of live.perSecond.keys()) if (second <= now - SPARK_BARS) live.perSecond.delete(second);
  const values = Array.from({ length: SPARK_BARS }, (_, i) => live.perSecond.get(now - SPARK_BARS + 1 + i) ?? 0);
  const peak = Math.max(...values);
  values.forEach((v, i) => {
    const height = v ? Math.max(3, (v / peak) * 54) : 1.5;
    const bar = sparkBars[i];
    bar.setAttribute("height", height);
    bar.setAttribute("y", 56 - height);
    bar.style.height = `${height}px`;
    bar.style.y = `${56 - height}px`;
    bar.classList.toggle("zero", !v);
  });
  const total = values.reduce((a, b) => a + b, 0);
  $("#rate-now").textContent = values.at(-2).toLocaleString();   // the last complete second
  $("#rate-peak").textContent = peak.toLocaleString();
  $("#rate-total").textContent = `${total.toLocaleString()} change${total === 1 ? "" : "s"}`;

  if (live.committedAt) {
    const ago = Math.max(0, Math.round((Date.now() - live.committedAt) / 1000));
    $("#commit-since").textContent = ago < 2 ? "just now" : ago < 120 ? `${ago} s ago` : `${Math.round(ago / 60)} min ago`;
  }
  let waiting = 0;
  for (const { d } of live.events) if (d.seq && d.seq > live.committed && d.kind !== "offset") waiting++;
  $("#batching-count").replaceChildren(...(waiting ? [h("span", { class: "badge pending" }, `${waiting.toLocaleString()} batching`)] : []));
}
setInterval(tick, 1000);

function filters() {
  return {
    table: $("#f-table").value,
    kinds: new Set($$(".f-op").filter((c) => c.checked).map((c) => c.value)),
    text: $("#f-text").value.trim().toLowerCase(),
  };
}
let currentFilters = filters();

function matches(item) {
  const f = currentFilters;
  if (!f.kinds.has(item.d.kind)) return false;
  if (f.table && item.d.table !== f.table) return false;
  return !f.text || item.text.includes(f.text);
}

/** "shop.orders" with the database faded. */
function tableNodes(table) {
  const dot = table.indexOf(".");
  return dot < 0 ? [table] : [h("span", { class: "db" }, table.slice(0, dot + 1)), table.slice(dot + 1)];
}

function row(item, fresh) {
  const { d } = item;
  let badge = null;
  if (d.seq) {
    const landed = live.committed && d.seq <= live.committed;
    badge = h("span");
    setLakeBadge(badge, landed);
    if (!landed) live.pendingRows.push({ seq: d.seq, badge });
  }
  return h("tr", { class: `k-${d.kind}${fresh ? " fresh" : ""}`, onclick: () => openItem(item) },
    h("td", { class: "time mono" }, fmtTime(d.time)),
    h("td", {}, h("span", { class: `badge ${d.kind}` }, d.label)),
    h("td", { class: "tbl" }, tableNodes(d.table)),
    h("td", { class: "key mono" }, d.key ?? ""),
    h("td", { class: "data mono" }, jsonNodes(item.preview, true)),
    h("td", { class: "seq mono" }, seqNodes(d.seq)),
    h("td", { class: "lake" }, badge));
}

// ---------------------------------------------------------------------------------- event modal

const modal = { event: null };
const MAX_HIGHLIGHT = 300_000;   // larger events are shown as plain text

/** Opens a feed event; a large one is fetched whole from the server. */
async function openItem(item) {
  if (item.raw) return openEvent(JSON.parse(item.raw));
  if (item.d.seq) {
    const response = await fetch(`/api/events/find?seq=${encodeURIComponent(item.d.seq)}`);
    if (response.ok) return openEvent(await response.json());
  }
  toast(`This event (${fmtBytes(item.size)}) is no longer in the live file`);
}

/** Shows one event, pretty-printed, in the modal. */
function openEvent(e) {
  const d = describe(e);
  modal.event = e;
  $("#modal-title").replaceChildren(
    h("span", { class: `badge ${d.kind}` }, d.label),
    h("strong", {}, d.table),
    d.key ? h("span", { class: "mono" }, d.key) : null,
    h("span", { class: "muted mono" }, fmtTime(d.time)));
  const text = JSON.stringify(e, null, 2);
  $("#modal-body").replaceChildren(...(text.length > MAX_HIGHLIGHT ? [text] : jsonNodes(text)));
  $("#event-modal").showModal();
  $("#modal-body").scrollTop = 0;
}

$("#modal-close").addEventListener("click", () => $("#event-modal").close());
// A click on the backdrop lands on the dialog itself; clicks on its content land on children.
$("#event-modal").addEventListener("click", (event) => { if (event.target === event.currentTarget) event.currentTarget.close(); });
$("#modal-copy").addEventListener("click", async () => {
  try {
    await navigator.clipboard.writeText(JSON.stringify(modal.event, null, 2));
    toast("Event JSON copied");
  } catch {
    toast("Copy failed: the clipboard needs localhost or HTTPS");
  }
});

function renderFeed() {
  draw.queue = [];
  live.pendingRows = [];
  const feed = $("#feed");
  const shown = [];
  for (let i = live.events.length - 1; i >= 0 && shown.length < MAX_ROWS; i--) {
    if (matches(live.events[i])) shown.push(row(live.events[i], false));
  }
  feed.replaceChildren(...shown);
  $("#feed-empty").hidden = shown.length > 0;
  updateCounts();
}

function resetLive() {
  live.events = [];
  live.pendingRows = [];
  live.perSecond.clear();
  for (const kind of Object.keys(live.counts)) live.counts[kind] = 0;
  renderFeed();
  tick();
}

function updatePauseButton() {
  const button = $("#pause");
  button.classList.toggle("primary", live.paused);
  button.replaceChildren(
    icon(live.paused ? "play" : "pause"),
    h("span", {}, live.paused ? "Resume" : "Pause"),
    live.paused && live.missed ? h("span", { class: "count-new" }, `+${live.missed.toLocaleString()}`) : null);
}

function togglePause() {
  live.paused = !live.paused;
  live.missed = 0;
  updatePauseButton();
  if (!live.paused) renderFeed();
}

for (const input of [...$$(".f-op"), $("#f-table"), $("#f-text")]) {
  input.addEventListener("input", () => { currentFilters = filters(); renderFeed(); });
}
$("#pause").addEventListener("click", togglePause);
$("#clear").addEventListener("click", resetLive);

function connect() {
  const pill = $("#conn-pill");
  const source = new EventSource("/api/events");
  let opened = false;
  source.onopen = () => {
    // A reconnect replays the backlog: start again from it.
    if (opened) resetLive();
    opened = true;
    live.catchingUp = true;
    pill.textContent = "stream: live";
    pill.className = "pill ok";
  };
  source.onmessage = (message) => {
    try { onEvent(message.data); } catch (error) { console.warn("bad event", error); }
  };
  // The server skipped ahead because this page fell too far behind the stream.
  source.addEventListener("gap", (message) => {
    const { bytes } = JSON.parse(message.data);
    showStreamNotice(`Fell behind the stream: skipped ${fmtBytes(bytes)} of events (the lake has them all)`);
  });
  source.addEventListener("caught-up", () => {
    if (!live.catchingUp) return;
    live.catchingUp = false;
    renderFeed();
    tick();
  });
  source.onerror = () => {
    pill.textContent = "stream: reconnecting";
    pill.className = "pill bad";
    fetch("/api/auth/me").catch(() => {});   // redirects to the sign-in page if the session ended
  };
}
connect();

async function pollStatus() {
  try {
    const status = await (await fetch("/api/status")).json();
    const pill = $("#lock-pill");
    const held = status.lock?.state === "leased";
    pill.textContent = status.lock ? `lock: ${held ? "held" : status.lock.state}` : "lock: none yet";
    pill.className = `pill ${held ? "ok" : "bad"}`;
    pill.title = held
      ? "A Vectezium instance holds the single-writer lease on cdc/shop/_lock"
      : "No instance holds the lease: Vectezium is stopped (or starting)";
    if (status.offset && status.offset.seq > live.committed) setCommitted(status.offset);
    showHeldSnapshots(status.offset?.checkpoint?.held_tables ?? []);
  } catch { /* the next poll retries */ }
}

/** Tables whose unplanned snapshot Vectezium is holding (`unplanned_snapshots: hold`). */
function showHeldSnapshots(tables) {
  const banner = $("#held-banner");
  banner.hidden = tables.length === 0;
  if (!tables.length) return;
  const names = tables.map(([db, table]) => `${db}.${table}`).join(" ");
  $("#held-tables").replaceChildren(...tables.map(([db, table]) => h("code", {}, `${db}.${table}`)));
  $("#held-snapshot").textContent = `./scripts/snapshot.sh ${names}`;
  $("#held-dismiss").textContent = `./scripts/snapshot.sh --dismiss ${names}`;
}
// Polled only while the page is visible: a forgotten background tab costs the lake nothing.
pollStatus();
setInterval(() => { if (!document.hidden) pollStatus(); }, 5000);
document.addEventListener("visibilitychange", () => { if (!document.hidden) pollStatus(); });

// ----------------------------------------------------------------------------------------- lake

// Folders are listed one at a time, when opened: listing the whole lake on every refresh grows
// with every batch Vectezium writes, and the storage account is the one it writes to.
const lake = {
  folders: new Map(),    // folder (relative to the root) → its listing
  open: new Set(),       // folders the viewer has opened
  selected: null,
  timer: null,
};
const MAX_LISTED_FILES = 300;

async function listFolder(prefix) {
  const data = await (await fetch(`/api/lake?prefix=${encodeURIComponent(prefix)}`)).json();
  $("#lake-container").textContent = data.container;
  $("#lake-root").textContent = data.root;
  const before = lake.folders.get(prefix);
  const known = before ? new Set(before.files.map((f) => f.name)) : null;
  data.fresh = new Set(known ? data.files.map((f) => f.name).filter((n) => !known.has(n)) : []);
  lake.folders.set(prefix, data);
  return data;
}

/** Re-lists the root and every open folder, then redraws the tree. */
async function loadLake() {
  await Promise.all(["", ...lake.open].map(listFolder));
  renderLake();
}

function renderLake() {
  const render = (prefix) => {
    const data = lake.folders.get(prefix);
    if (!data) return [h("div", { class: "file muted" }, "Loading…")];
    const files = data.files.slice(0, MAX_LISTED_FILES);
    return [
      ...data.folders.map((name) => {
        const full = `${prefix}${name}/`;
        const details = h("details", { open: lake.open.has(full) },
          h("summary", {}, icon("chevron", "chev"), icon("folder", "folder"), h("span", { class: "spacer" }, name)),
          lake.open.has(full) ? render(full) : []);
        details.addEventListener("toggle", async () => {
          // A redraw creates open folders already open, and the browser reports that as a
          // toggle too: only a real change (a click) may list and redraw, or it never stops.
          if (details.open === lake.open.has(full)) return;
          if (!details.open) { lake.open.delete(full); return; }
          lake.open.add(full);
          if (!lake.folders.has(full)) await listFolder(full);
          renderLake();
        });
        return details;
      }),
      ...files.map((file) =>
        h("div", {
          class: `file${file.name === lake.selected ? " selected" : ""}${data.fresh.has(file.name) ? " new" : ""}`,
          title: `${file.name}\n${file.last_modified}`,
          onclick: (event) => {
            $$(".tree .file").forEach((f) => f.classList.remove("selected"));
            event.currentTarget.classList.add("selected");
            showBlob(file.name);
          },
        }, icon("file"), h("span", { class: "name" }, file.name.split("/").at(-1)), h("span", { class: "count" }, fmtBytes(file.size)))),
      data.files.length > files.length
        ? h("div", { class: "file muted" }, `${(data.files.length - files.length).toLocaleString()} older files not shown`)
        : null,
    ];
  };
  const root = lake.folders.get("");
  const nodes = render("");
  $("#lake-tree").replaceChildren(...(root && !root.folders.length && !root.files.length
    ? [h("div", { class: "empty" },
      h("div", { class: "empty-icon" }, icon("waves")),
      h("div", { class: "empty-title" }, "No files yet"),
      "Rows land within about 10 seconds of the first change.")]
    : nodes));
}

async function showBlob(name) {
  lake.selected = name;
  const preview = $("#lake-preview");
  preview.replaceChildren(h("div", { class: "empty" }, "Loading…"));
  const response = await fetch(`/api/lake/blob?name=${encodeURIComponent(name)}`);
  if (!response.ok) {
    preview.replaceChildren(h("div", { class: "empty" }, `Could not read ${name}: ${response.status}`));
    return;
  }
  const blob = await response.json();
  // A large file is only read up to its first 5,000 lines, so its totals are unknown.
  const meta = blob.truncated
    ? `${fmtBytes(blob.stored_bytes)} stored · showing the first ${blob.records.length.toLocaleString()} lines (the rest is not read)`
    : `${blob.lines.toLocaleString()} line${blob.lines === 1 ? "" : "s"} · ` +
      (name.endsWith(".gz") ? `${fmtBytes(blob.stored_bytes)} gzipped, ${fmtBytes(blob.bytes)} as JSON` : fmtBytes(blob.bytes));
  const records = blob.records;
  let body;
  if (records.length && records.every((r) => r && r.metadata && "record" in r)) {
    body = h("div", { class: "scroll" }, h("table", { class: "grid" },
      h("thead", {}, h("tr", {}, ["op", "seq", "key", "snapshot", "before", "after"].map((c) => h("th", {}, c)))),
      h("tbody", {}, records.map((r) => {
        const { before, after } = images(r);
        const cell = (image) => h("td", { class: "json", title: image ? JSON.stringify(image, null, 2) : "" },
          image ? compact(image) : "");
        return h("tr", { class: "clickable", onclick: () => openEvent(r) },
          h("td", {}, h("span", { class: `badge ${r.metadata.op}` }, r.metadata.op)),
          h("td", { class: "mono" }, seqNodes(r.metadata.seq)),
          h("td", { class: "mono" }, keyText(r.metadata.key)),
          h("td", {}, r.metadata.snapshot === "false" ? "" : r.metadata.snapshot),
          cell(before),
          cell(after));
      }))));
  } else {
    body = h("div", { class: "scroll" }, records.slice(0, 200).map((r) => h("pre", {}, typeof r === "string" ? r : JSON.stringify(r, null, 2))));
  }
  preview.replaceChildren(h("h3", { class: "mono" }, name), h("div", { class: "meta" }, meta), body);
}

function scheduleLake() {
  clearInterval(lake.timer);
  if ($("#lake-auto").checked) {
    lake.timer = setInterval(() => {
      if ($("#tab-lake").classList.contains("active") && !document.hidden) loadLake();
    }, 10000);
  }
}
$("#lake-refresh").addEventListener("click", loadLake);
$("#lake-auto").addEventListener("change", scheduleLake);
tabLoaders.lake = () => { loadLake(); scheduleLake(); };

// ---------------------------------------------------------------------------------------- MySQL

const db = { selected: null };

async function loadTables() {
  const data = await (await fetch("/api/mysql/tables")).json();
  if (!db.selected && data.tables.length) db.selected = data.tables[0].name;
  $("#mysql-tables").replaceChildren(...data.tables.map((t) =>
    h("button", { class: t.name === db.selected ? "active" : "", onclick: () => { db.selected = t.name; loadTables(); } },
      t.name, h("span", { class: "muted", title: "Estimated by MySQL (no COUNT(*), which would scan the table)" },
        `~${t.rows.toLocaleString()}`))));
  if (db.selected) loadRows(db.selected);
}

async function loadRows(table) {
  const data = await (await fetch(`/api/mysql/rows?table=${encodeURIComponent(table)}&limit=200`)).json();
  $("#mysql-rows").replaceChildren(
    h("div", { class: "meta" }, `${table}: newest 200 rows by primary key (${data.key.join(", ") || "no key"})`),
    h("div", { class: "scroll" }, h("table", { class: "grid" },
      h("thead", {}, h("tr", {}, data.columns.map((c) => h("th", {}, c)))),
      h("tbody", {}, data.rows.map((r) => h("tr", {}, data.columns.map((c) =>
        h("td", { class: r[c] === null ? "mono null" : "mono" }, r[c] === null ? "NULL" : typeof r[c] === "object" ? compact(r[c]) : r[c]))))))));
}

$("#mysql-refresh").addEventListener("click", loadTables);
tabLoaders.mysql = loadTables;

// ------------------------------------------------------------------------------------- licences

let licencesLoaded = false;

async function loadLicences() {
  if (licencesLoaded) return;
  const data = await (await fetch("/api/licences")).json();
  const missing = "Not found: mount the files from vectezium/ at /licences (see docker-compose.yml).";
  $("#licence-notice").textContent = data.notice ?? missing;
  $("#licence-text").textContent = data.license ?? missing;
  $("#licence-crates").replaceChildren(data.crates.length
    ? h("table", { class: "grid" },
      h("thead", {}, h("tr", {}, ["Crate", "Licence", "Copyright"].map((c) => h("th", {}, c)))),
      h("tbody", {}, data.crates.map((c) => h("tr", {},
        h("td", {}, h("a", { href: c.Origin, target: "_blank", rel: "noopener" }, c.Component)),
        h("td", {}, c.License),
        h("td", {}, c.Copyright)))))
    : h("div", { class: "empty" }, missing));
  licencesLoaded = true;
}

tabLoaders.licences = loadLicences;

// A link such as /#lake opens that tab.
if ($(`button.tab[data-tab="${location.hash.slice(1)}"]`)) showTab(location.hash.slice(1));

// ------------------------------------------------------------------------------------ shortcuts

addEventListener("keydown", (event) => {
  if (event.ctrlKey || event.metaKey || event.altKey || $("#event-modal").open) return;
  const typing = event.target.closest("input, select, textarea");
  if (typing) {
    if (event.key === "Escape" && event.target.id === "f-text") {
      event.target.value = "";
      event.target.dispatchEvent(new Event("input"));
      event.target.blur();
    }
    return;
  }
  const tabs = ["live", "lake", "mysql", "licences"];
  if (event.key >= "1" && event.key <= "4") showTab(tabs[event.key - 1]);
  else if (event.key === "/") { event.preventDefault(); showTab("live"); $("#f-text").focus(); }
  else if (event.key === "p" || event.key === "P") togglePause();
  else if (event.key === "t" || event.key === "T") nextTheme();
  else return;
  event.preventDefault();
});
