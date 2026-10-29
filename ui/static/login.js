"use strict";

// The sign-in page: sign in, choose a new password when the account still has the default one,
// then go to the UI. The hero on the left is decoration: a sketch of the pipeline in motion.

const $ = (selector, root = document) => root.querySelector(selector);
const reduceMotion = matchMedia("(prefers-reduced-motion: reduce)");
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

// ---------------------------------------------------------------------------------------- theme

const darkQuery = matchMedia("(prefers-color-scheme: dark)");
const THEMES = ["system", "light", "dark"];
const THEME_ICONS = { system: "monitor", light: "sun", dark: "moon" };
const THEME_NAMES = { system: "Theme: match system", light: "Theme: light", dark: "Theme: dark" };
const themePref = () => document.documentElement.dataset.themePref || "system";
const resolveTheme = (pref) => pref === "dark" || (pref === "system" && darkQuery.matches) ? "dark" : "light";

function syncThemeButton() {
  const button = $("#theme-toggle");
  $("use", button).setAttribute("href", `#i-${THEME_ICONS[themePref()]}`);
  button.title = THEME_NAMES[themePref()];
}

/** Switches theme; with view transitions the new theme spreads in a circle from the button. */
function setTheme(pref) {
  try { localStorage.setItem("theme", pref); } catch { /* private mode: not remembered */ }
  const root = document.documentElement;
  const next = resolveTheme(pref);
  const apply = () => { root.dataset.themePref = pref; root.dataset.theme = next; syncThemeButton(); flow.recolour(); };
  if (next === root.dataset.theme || !document.startViewTransition || reduceMotion.matches) { apply(); return; }
  const r = $("#theme-toggle").getBoundingClientRect();
  const x = r.left + r.width / 2, y = r.top + r.height / 2;
  const radius = Math.hypot(Math.max(x, innerWidth - x), Math.max(y, innerHeight - y));
  document.startViewTransition(apply).ready.then(() => document.documentElement.animate(
    { clipPath: [`circle(0px at ${x}px ${y}px)`, `circle(${radius}px at ${x}px ${y}px)`] },
    { duration: 650, easing: "cubic-bezier(.4, 0, .2, 1)", pseudoElement: "::view-transition-new(root)" }));
}

$("#theme-toggle").addEventListener("click", () => setTheme(THEMES[(THEMES.indexOf(themePref()) + 1) % THEMES.length]));
darkQuery.addEventListener("change", () => { if (themePref() === "system") setTheme("system"); });

// ------------------------------------------------------------------------- the pipeline sketch

const TABLES = ["customers", "orders", "order_items", "products"];
const FIRST = ["Ada", "Kofi", "Mei", "Lars", "Pia", "Omar", "Zara", "Theo", "Ines", "Ravi"];
const LAST = ["Okafor", "Lindqvist", "Costa", "Tanaka", "Haddad", "Novak", "Mensah", "Silva"];
const TIERS = ["bronze", "silver", "gold"];
const pick = (list) => list[Math.floor(Math.random() * list.length)];
const rand = (lo, hi) => Math.floor(lo + Math.random() * (hi - lo));

/** A plausible change, in the shape the UI's live feed shows. */
function fakeEvent() {
  const op = Math.random() < 0.45 ? "c" : Math.random() < 0.85 ? "u" : "d";
  const table = pick(TABLES);
  const id = rand(1000, 9999);
  let json;
  if (table === "customers") {
    json = op === "u"
      ? { before: { loyalty_tier: "bronze" }, after: { loyalty_tier: pick(TIERS.slice(1)) } }
      : { id, name: `${pick(FIRST)} ${pick(LAST)}`, country: pick(["GB", "IE", "FR", "NG", "SE"]), loyalty_tier: pick(TIERS) };
  } else if (table === "orders") {
    json = op === "u" ? { before: { status: "placed" }, after: { status: pick(["paid", "shipped"]) } }
      : { id, customer_id: rand(1, 6000), status: "placed", total: (Math.random() * 300).toFixed(2) };
  } else if (table === "order_items") {
    json = { order_id: id, line_no: rand(1, 5), product_id: rand(1, 250), qty: rand(1, 4) };
  } else {
    const stock = rand(20, 200);
    json = op === "u" ? { before: { stock }, after: { stock: stock - rand(1, 4) } }
      : { id, sku: `SKU-${id}`, price: (Math.random() * 90 + 5).toFixed(2), stock };
  }
  return { op, table, json };
}

const OP_NAMES = { c: "insert", u: "update", d: "delete" };

// The ticker under the sketch: the newest three events.
const ticker = {
  push(event) {
    const list = $("#ticker");
    if (!list) return;
    const row = document.createElement("div");
    row.className = `tick k-${event.op}`;
    const op = Object.assign(document.createElement("span"), { className: "op", textContent: OP_NAMES[event.op] });
    const table = Object.assign(document.createElement("span"), { className: "tbl", textContent: `shop.${event.table}` });
    const json = Object.assign(document.createElement("span"), { className: "json", textContent: JSON.stringify(event.json) });
    row.append(op, table, json);
    list.prepend(row);
    const rows = [...list.children];
    for (const extra of rows.slice(3)) {
      if (extra.classList.contains("out")) continue;
      extra.classList.add("out");
      extra.addEventListener("animationend", () => extra.remove(), { once: true });
      if (reduceMotion.matches) extra.remove();
    }
  },
};

const flow = (() => {
  const canvas = $("#flow");
  const stage = $(".stage");
  if (!canvas || getComputedStyle($(".hero")).display === "none") return { recolour() {} };
  const ctx = canvas.getContext("2d");
  const nodes = [$(".n-src"), $(".n-vtz"), $(".n-lake")];
  let colours = {}, width = 0, height = 0, points = [];
  const dots = [], files = [];
  let batch = [], filesLanded = 0, lastSpawn = 0, lastFlush = 0, nextSpawn = 400;

  function recolour() {
    const css = getComputedStyle(document.documentElement);
    const v = (name) => css.getPropertyValue(name).trim();
    colours = { c: v("--c"), u: v("--u"), d: v("--d"), track: v("--track"), accent: v("--accent"), accent2: v("--accent-2") };
  }

  function layout() {
    const dpr = Math.min(devicePixelRatio || 1, 2);
    width = stage.clientWidth; height = stage.clientHeight;
    canvas.width = width * dpr; canvas.height = height * dpr;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    const box = stage.getBoundingClientRect();
    points = nodes.map((node) => {
      const r = $(".node-icon", node).getBoundingClientRect();
      return { x: r.left + r.width / 2 - box.left, y: r.top + r.height / 2 - box.top, half: r.width / 2 };
    });
  }

  // Each leg is a gentle S-curve between two nodes; t runs 0..1 along it.
  function along(leg, t) {
    const a = points[leg], b = points[leg + 1];
    const x0 = a.x + a.half + 6, x1 = b.x - b.half - 6;
    const bend = leg === 0 ? -26 : 26;
    const u = 1 - t;
    const cx0 = x0 + (x1 - x0) * 0.35, cx1 = x0 + (x1 - x0) * 0.65;
    return {
      x: u * u * u * x0 + 3 * u * u * t * cx0 + 3 * u * t * t * cx1 + t * t * t * x1,
      y: u * u * u * a.y + 3 * u * u * t * (a.y + bend) + 3 * u * t * t * (b.y - bend) + t * t * t * b.y,
    };
  }

  function drawTrack(leg) {
    ctx.beginPath();
    for (let i = 0; i <= 40; i++) {
      const p = along(leg, i / 40);
      i ? ctx.lineTo(p.x, p.y) : ctx.moveTo(p.x, p.y);
    }
    ctx.strokeStyle = colours.track;
    ctx.lineWidth = 2;
    ctx.setLineDash([2, 6]);
    ctx.lineCap = "round";
    ctx.stroke();
    ctx.setLineDash([]);
  }

  function spawn() {
    const event = fakeEvent();
    dots.push({ t: 0, speed: 0.00042 + Math.random() * 0.00022, event });
    ticker.push(event);
  }

  function flush(now) {
    if (!batch.length) return;
    files.push({ t: 0, count: batch.length });
    batch = [];
    lastFlush = now;
    $("#batch-label").textContent = "flushing";
    replay($(".n-vtz"), "flush");
  }

  let last = performance.now();
  function frame(now) {
    const dt = Math.min(now - last, 50);
    last = now;
    if (now - lastSpawn > nextSpawn) { spawn(); lastSpawn = now; nextSpawn = 500 + Math.random() * 1100; }
    if (now - lastFlush > 3600) flush(now);

    ctx.clearRect(0, 0, width, height);
    drawTrack(0);
    drawTrack(1);

    // Events: a glowing dot with a short tail, in the colour of its operation.
    for (let i = dots.length - 1; i >= 0; i--) {
      const dot = dots[i];
      dot.t += dot.speed * dt;
      if (dot.t >= 1) {
        dots.splice(i, 1);
        batch.push(dot.event);
        $("#batch-label").textContent = `batching · ${batch.length}`;
        continue;
      }
      const colour = colours[dot.event.op];
      for (let k = 6; k >= 0; k--) {
        const p = along(0, Math.max(0, dot.t - k * 0.012));
        ctx.globalAlpha = k ? (1 - k / 7) * 0.35 : 1;
        ctx.beginPath();
        ctx.arc(p.x, p.y, k ? 3 - k * 0.3 : 4, 0, Math.PI * 2);
        ctx.fillStyle = colour;
        ctx.shadowColor = colour;
        ctx.shadowBlur = k ? 0 : 12;
        ctx.fill();
      }
      ctx.globalAlpha = 1;
      ctx.shadowBlur = 0;
    }

    // Files: a small gradient block carrying a batch to the lake.
    for (let i = files.length - 1; i >= 0; i--) {
      const file = files[i];
      file.t += 0.00055 * dt;
      if (file.t >= 1) {
        files.splice(i, 1);
        filesLanded++;
        $("#files-label").textContent = `${filesLanded} file${filesLanded === 1 ? "" : "s"}`;
        $("#batch-label").textContent = "batching";
        replay($(".n-lake"), "landed");
        continue;
      }
      const eased = file.t < 0.5 ? 2 * file.t * file.t : 1 - (-2 * file.t + 2) ** 2 / 2;
      const p = along(1, eased);
      const gradient = ctx.createLinearGradient(p.x - 10, p.y - 8, p.x + 10, p.y + 8);
      gradient.addColorStop(0, colours.accent);
      gradient.addColorStop(1, colours.accent2);
      ctx.fillStyle = gradient;
      ctx.shadowColor = colours.accent;
      ctx.shadowBlur = 18;
      ctx.beginPath();
      ctx.roundRect(p.x - 11, p.y - 8, 22, 16, 4);
      ctx.fill();
      ctx.shadowBlur = 0;
      ctx.fillStyle = "#fff";
      ctx.font = "600 9px ui-sans-serif, system-ui, sans-serif";
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      ctx.fillText(String(file.count), p.x, p.y + 0.5);
    }
    requestAnimationFrame(frame);
  }

  recolour();
  layout();
  new ResizeObserver(layout).observe(stage);
  if (reduceMotion.matches) {
    drawTrack(0);
    drawTrack(1);
    for (let i = 0; i < 3; i++) ticker.push(fakeEvent());
  } else {
    requestAnimationFrame(frame);
  }
  return { recolour };
})();

/** Restarts a CSS animation class on an element. */
function replay(node, cls) {
  node.classList.remove(cls);
  void node.offsetWidth;
  node.classList.add(cls);
}

// The card's rim and glow follow the pointer.
const card = $("#card");
card.addEventListener("pointermove", (e) => {
  const r = card.getBoundingClientRect();
  card.style.setProperty("--mx", `${e.clientX - r.left}px`);
  card.style.setProperty("--my", `${e.clientY - r.top}px`);
});

// ------------------------------------------------------------------------------------- the API

async function api(path, body) {
  const response = await fetch(path, {
    method: body === undefined ? "GET" : "POST",
    headers: body === undefined ? {} : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
    credentials: "same-origin",
  });
  let data = {};
  try { data = await response.json(); } catch { /* empty body */ }
  if (!response.ok) {
    const detail = Array.isArray(data.detail) ? "Fill in both fields." : data.detail;
    throw Object.assign(new Error(detail || `Something went wrong (${response.status}).`), { status: response.status });
  }
  return data;
}

// ------------------------------------------------------------------------------------- steps

let currentPassword = null;   // kept from step 1 so step 2 needn't ask for it again

async function show(id) {
  const next = $(`#${id}`);
  const current = $(".step.active");
  if (current === next) return;
  if (current) {
    current.classList.remove("active");
    if (!reduceMotion.matches) {
      current.classList.add("leaving");
      await sleep(230);
      current.classList.remove("leaving");
    }
  }
  next.classList.add("active");
  $("input:not([hidden]):not([type=hidden])", next)?.focus();
}

function showError(id, message) {
  const box = $(`#${id}`);
  $("span", box).textContent = message;
  box.hidden = false;
  replay(card, "shake");
}

function busy(form, on) {
  const button = $(".submit", form);
  button.classList.toggle("busy", on);
  button.disabled = on || (form.id === "step-change" && !rulesPass());
}

async function enter(username) {
  $("#done-title").textContent = `You're in, ${username}`;
  await show("step-done");
  await sleep(reduceMotion.matches ? 300 : 1300);
  location.replace("/");
}

// Step 1: sign in.
$("#step-login").addEventListener("submit", async (e) => {
  e.preventDefault();
  const form = e.currentTarget;
  const username = $("#username").value.trim();
  const password = $("#password").value;
  $("#login-error").hidden = true;
  if (!username || !password) {
    $(username ? "#password" : "#username").closest(".field").classList.add("invalid");
    showError("login-error", "Enter your username and password.");
    return;
  }
  busy(form, true);
  try {
    const user = await api("/api/auth/login", { username, password });
    if (user.must_change) {
      currentPassword = password;
      $("#change-username").value = user.username;
      $("#current-field").hidden = true;
      await show("step-change");
    } else {
      await enter(user.username);
    }
  } catch (error) {
    showError("login-error", error.message);
    $("#password").select();
  } finally {
    busy(form, false);
  }
});

for (const input of document.querySelectorAll(".field input")) {
  input.addEventListener("input", () => input.closest(".field").classList.remove("invalid"));
}

// Caps Lock warning on password fields.
for (const input of document.querySelectorAll("input[type=password]")) {
  input.addEventListener("keyup", (e) => {
    const caps = $(".caps");
    if (caps && e.getModifierState) caps.hidden = !e.getModifierState("CapsLock");
  });
}

// Show / hide password.
for (const button of document.querySelectorAll(".reveal")) {
  button.addEventListener("click", () => {
    const input = document.getElementById(button.dataset.for);
    const show = input.type === "password";
    input.type = show ? "text" : "password";
    $("use", button).setAttribute("href", show ? "#i-eye-off" : "#i-eye");
    button.setAttribute("aria-label", show ? "Hide password" : "Show password");
    input.focus();
  });
}

// Step 2: choose a new password.
const STRENGTH = ["", "Weak", "Fair", "Good", "Strong"];

function score(password) {
  if (!password) return 0;
  let points = 0;
  if (password.length >= 8) points++;
  if (password.length >= 12) points++;
  if (/[a-z]/.test(password) && /[A-Z]/.test(password)) points++;
  if (/\d/.test(password)) points++;
  if (/[^A-Za-z0-9]/.test(password)) points++;
  if (new Set(password).size < 4) points = 1;
  return Math.max(1, Math.min(4, points));
}

function rules() {
  const pw = $("#new-password").value;
  const confirm = $("#confirm-password").value;
  return {
    length: pw.length >= 8,
    mix: /[A-Za-z]/.test(pw) && /[^A-Za-z]/.test(pw),
    fresh: pw.length > 0 && pw.toLowerCase() !== "admin" && pw !== currentPassword,
    match: pw.length > 0 && pw === confirm,
  };
}
const rulesPass = () => Object.values(rules()).every(Boolean);

function updateRules() {
  const result = rules();
  for (const li of document.querySelectorAll("#rules li")) li.classList.toggle("ok", result[li.dataset.rule]);
  const pw = $("#new-password").value;
  const s = score(pw);
  $(".meter").dataset.score = s;
  $("#meter-label").textContent = pw ? `Strength: ${STRENGTH[s]}` : " ";
  $("#step-change .submit").disabled = !rulesPass();
}
$("#new-password").addEventListener("input", updateRules);
$("#confirm-password").addEventListener("input", updateRules);

$("#step-change").addEventListener("submit", async (e) => {
  e.preventDefault();
  const form = e.currentTarget;
  $("#change-error").hidden = true;
  if (!rulesPass()) return;
  const current = currentPassword ?? $("#current-password").value;
  if (!current) {
    $("#current-field").classList.add("invalid");
    showError("change-error", "Enter your current password.");
    return;
  }
  busy(form, true);
  try {
    const user = await api("/api/auth/password", { current, new: $("#new-password").value });
    currentPassword = null;
    await enter(user.username);
  } catch (error) {
    if (error.status === 401) { await show("step-login"); showError("login-error", "Your session ended. Sign in again."); return; }
    showError("change-error", error.message);
  } finally {
    busy(form, false);
  }
});

$("#change-cancel").addEventListener("click", async () => {
  try { await api("/api/auth/logout", {}); } catch { /* signed out either way */ }
  currentPassword = null;
  $("#password").value = "";
  $("#new-password").value = $("#confirm-password").value = "";
  updateRules();
  await show("step-login");
});

// ------------------------------------------------------------------------------------- start

(async () => {
  syncThemeButton();
  try {
    const { first_run } = await api("/api/auth/state");
    $("#first-run-hint").hidden = !first_run;
  } catch { /* the hint stays hidden */ }
  try {
    const user = await api("/api/auth/me");
    if (!user.must_change) { location.replace("/"); return; }
    // Signed in but the password still has to change (for example after a reload).
    $("#change-username").value = user.username;
    $("#current-field").hidden = false;
    await show("step-change");
  } catch {
    $("#username").focus();
  }
})();
