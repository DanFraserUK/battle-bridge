// Battle Bridge page.  Polls /api/live and updates the page in place.
// Nothing is re-rendered wholesale: elements are made once and their text
// changed, so a redraw never swallows a click.
"use strict";

const LIVE_MS = 2000;
const $ = (id) => document.getElementById(id);
const SVGNS = "http://www.w3.org/2000/svg";

const state = { live: null, failures: 0, loggedIn: null };

// --- formatting ------------------------------------------------------------

function fmtBytes(n, digits = 1) {
  if (n == null) return "–";
  const u = ["B", "KiB", "MiB", "GiB", "TiB"];
  let i = 0;
  while (Math.abs(n) >= 1024 && i < u.length - 1) { n /= 1024; i++; }
  return `${n.toFixed(i === 0 ? 0 : digits)} ${u[i]}`;
}
function fmtRate(n) {
  if (n == null) return "–";
  const bits = n * 8;
  const u = ["b/s", "kb/s", "Mb/s", "Gb/s"];
  let v = bits, i = 0;
  while (v >= 1000 && i < u.length - 1) { v /= 1000; i++; }
  return `${v.toFixed(v < 10 && i > 0 ? 1 : 0)} ${u[i]}`;
}
function fmtDuration(sec) {
  if (sec == null || sec < 0) return "–";
  const d = Math.floor(sec / 86400), h = Math.floor((sec % 86400) / 3600), m = Math.floor((sec % 3600) / 60);
  if (d > 0) return `${d} d ${h} h`;
  if (h > 0) return `${h} h ${m} m`;
  return `${m} m`;
}
function fmtPct(v) { return v == null ? "–" : `${v.toFixed(v < 10 ? 1 : 0)}%`; }
function fmtUnit(v, unit) {
  if (v == null) return "–";
  if (unit === "%") return fmtPct(v);
  if (unit === "B/s") return fmtRate(v);
  if (unit === "°C") return `${Math.round(v)}°C`;
  if (unit === "MiB") return fmtBytes(v * 1048576);
  return v.toFixed(2);
}
function setText(el, text) { if (el.textContent !== text) el.textContent = text; }
function level(el, v, warn, hot) {
  el.classList.toggle("warn", v != null && v >= warn && v < hot);
  el.classList.toggle("hot", v != null && v >= hot);
}
function setBar(rect, pct, warn = 85, hot = 95) {
  const w = Math.max(0, Math.min(100, pct || 0));
  rect.setAttribute("width", w.toFixed(1));
  level(rect, pct, warn, hot);
}

// --- polling ---------------------------------------------------------------

async function poll() {
  try {
    const r = await fetch("/api/live", { cache: "no-store" });
    if (!r.ok) throw new Error(r.status);
    const data = await r.json();
    if (!data.has_password) { window.location.href = "/setup"; return; }
    state.live = data;
    window.BB.lastLive = data;
    state.failures = 0;
    render(data);
    const c = $("conn");
    c.className = "conn ok";
    setText(c, "Live");
  } catch (e) {
    state.failures++;
    if (state.failures >= 2) {
      const c = $("conn");
      c.className = "conn bad";
      setText(c, "Lost contact");
    }
  }
  setTimeout(poll, LIVE_MS);
}

function render(d) {
  setText($("clock"), new Date(d.now * 1000).toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" }));
  if (d.hostname) {
    setText($("hostname"), d.hostname);
    if (document.title !== `${d.hostname} · Battle Bridge`) document.title = `${d.hostname} · Battle Bridge`;
  }
  setLogin(d.logged_in);
  renderHost(d.host, d.host_error);
  renderGpu(d.gpu);
  renderServices(d.containers, d.docker_error);
  window.BB.renderFacts(d);
  window.BB.renderApis(d);
}

// --- system box ------------------------------------------------------------

function renderHost(h, err) {
  const errEl = $("host-err");
  errEl.hidden = !err;
  if (err) setText(errEl, `Host stats unavailable: ${err}`);
  if (!h) return;
  setText($("uptime"), fmtDuration(h.uptime));
  if (h.cpu_model) setText($("cpu-name"), h.cpu_model);
  if (h.kernel) setText($("kernel-name"), `Linux ${h.kernel}`);
  setText($("cpu-total"), fmtPct(h.cpu));
  const t = $("cpu-temp");
  setText(t, h.cpu_temp == null ? "–" : `${Math.round(h.cpu_temp)}°C`);
  level(t, h.cpu_temp, 75, 85);
  const m = $("mem");
  setText(m, `${fmtBytes(h.mem_used)} / ${fmtBytes(h.mem_total)}`);
  level(m, h.mem_pct, 85, 95);
  setBar($("mem-bar"), h.mem_pct);
  let sub = `${fmtPct(h.mem_pct)} used`;
  if (h.swap_total) sub += ` · swap ${fmtBytes(h.swap_used)}`;
  setText($("mem-sub"), sub);
  setText($("net-rx"), fmtRate(h.net_rx));
  setText($("net-tx"), fmtRate(h.net_tx));
  setText($("load"), h.load ? h.load.map((v) => v.toFixed(2)).join("  ") : "–");
  renderCores(h.cores);
}

function renderCores(cores) {
  const box = $("cores");
  if (box.children.length !== cores.length) {
    box.replaceChildren();
    cores.forEach((_, i) => {
      const b = document.createElement("button");
      b.type = "button";
      b.className = "core";
      b.dataset.series = `core.${i}`;
      b.dataset.label = `CPU core ${i}`;
      b.dataset.unit = "%";
      const name = document.createElement("span");
      name.textContent = `C${i}`;
      const svg = barSvg();
      const pct = document.createElement("span");
      pct.className = "pct";
      b.append(name, svg, pct);
      box.append(b);
    });
  }
  cores.forEach((v, i) => {
    const b = box.children[i];
    setBar(b.querySelector(".bar-fg"), v, 70, 90);
    setText(b.querySelector(".pct"), fmtPct(v));
  });
}

function barSvg(extra = "") {
  const svg = document.createElementNS(SVGNS, "svg");
  svg.setAttribute("class", "bar");
  svg.setAttribute("viewBox", "0 0 100 6");
  svg.setAttribute("preserveAspectRatio", "none");
  const bg = document.createElementNS(SVGNS, "rect");
  bg.setAttribute("class", "bar-bg");
  bg.setAttribute("width", "100");
  bg.setAttribute("height", "6");
  const fg = document.createElementNS(SVGNS, "rect");
  fg.setAttribute("class", `bar-fg ${extra}`.trim());
  fg.setAttribute("width", "0");
  fg.setAttribute("height", "6");
  svg.append(bg, fg);
  return svg;
}

function renderGpu(g) {
  if (!g) return;
  // No NVIDIA GPU (or none passed in): the column folds away.
  $("sys-gpu").hidden = !g.ok;
  document.querySelector(".sys").classList.toggle("no-gpu", !g.ok);
  if (!g.ok) return;
  setText($("gpu-name"), g.name);
  setText($("gpu-util"), fmtPct(g.util));
  const t = $("gpu-temp");
  setText(t, g.temp == null ? "–" : `${Math.round(g.temp)}°C`);
  level(t, g.temp, 75, 85);
  setText($("gpu-mem"), `${fmtBytes(g.mem_used * 1048576, 0)} / ${fmtBytes(g.mem_total * 1048576, 0)}`);
  setBar($("gpu-mem-bar"), g.mem_total ? (100 * g.mem_used) / g.mem_total : 0);
  const extra = [];
  if (g.power != null) extra.push(`${Math.round(g.power)} W`);
  if (g.encoders) extra.push(`${g.encoders} encoding`);
  setText($("gpu-extra"), extra.join("  "));
}

// --- services --------------------------------------------------------------

function iconClass(name) {
  let h = 0;
  for (const ch of name) h = (h * 31 + ch.charCodeAt(0)) >>> 0;
  return `ic-${h % 6}`;
}
function initials(label) {
  const words = label.replace(/[^A-Za-z0-9 ]/g, " ").split(/\s+/).filter(Boolean);
  if (words.length >= 2) return (words[0][0] + words[1][0]).toUpperCase();
  return label.slice(0, 2).toUpperCase();
}

function stateOf(c) {
  if (c.state === "running") {
    if (c.health === "unhealthy") return ["st-warn", "unhealthy"];
    if (c.health === "starting") return ["st-warn", "starting"];
    return ["st-up", c.health === "healthy" ? "healthy" : "up"];
  }
  if (c.state === "restarting") return ["st-warn", "restarting"];
  if (c.state === "paused") return ["st-warn", "paused"];
  return ["st-down", c.state === "exited" ? "stopped" : c.state];
}

function makeTile(c) {
  const t = document.createElement("div");
  t.className = "tile";
  t.dataset.name = c.name;
  const ic = document.createElement("div");
  ic.className = `mono-icon ${iconClass(c.name)}`;
  const name = document.createElement("div");
  name.className = "name";
  const st = document.createElement("div");
  st.className = "state";
  const meta = document.createElement("div");
  meta.className = "meta";
  const nums = document.createElement("div");
  nums.className = "nums";
  const cpu = document.createElement("button");
  cpu.type = "button";
  cpu.dataset.series = `ctr.${c.name}.cpu`;
  cpu.dataset.label = `${c.name} CPU`;
  cpu.dataset.unit = "%";
  cpu.title = "CPU, share of the whole machine.  Click for 24 hours.";
  const mem = document.createElement("button");
  mem.type = "button";
  mem.dataset.series = `ctr.${c.name}.mem`;
  mem.dataset.label = `${c.name} memory`;
  mem.dataset.unit = "MiB";
  mem.title = "Memory.  Click for 24 hours.";
  const gpu = document.createElement("button");
  gpu.type = "button";
  gpu.dataset.series = `ctr.${c.name}.gpu`;
  gpu.dataset.label = `${c.name} GPU`;
  gpu.dataset.unit = "%";
  gpu.title = "GPU, its busiest engine (compute, encode or decode).  Click for 24 hours.";
  gpu.hidden = true;
  nums.append(cpu, mem, gpu);
  t.append(ic, name, st, meta, nums);
  return t;
}

function updateTile(t, c, now) {
  const [cls, word] = stateOf(c);
  t.classList.remove("st-up", "st-warn", "st-down");
  t.classList.add(cls);
  const ic = t.children[0];
  setText(ic, initials(c.label));
  const nameEl = t.children[1];
  const a = nameEl.querySelector("a");
  if (c.link) {
    // Compare with the link as given: a.href is the browser's tidied copy,
    // which may never match, and would rebuild the link on every poll.
    if (!a || a.dataset.link !== c.link) {
      const link = document.createElement("a");
      link.href = c.link;
      link.dataset.link = c.link;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      link.textContent = c.label;
      nameEl.replaceChildren(link);
    } else {
      setText(a, c.label);
    }
  } else if (a || nameEl.textContent !== c.label) {
    nameEl.replaceChildren(document.createTextNode(c.label));
  }
  let stText = word;
  if (c.state === "running" && c.started) stText += ` · ${fmtDuration(now - c.started)}`;
  setText(t.children[2], stText);
  const published = (c.ports || []).filter((p) => p.public).map((p) => p.public);
  let meta = c.ip ? c.ip : c.host_network ? "host network" : published.length ? `port ${published.slice(0, 3).join(", ")}` : (c.networks || []).join(", ");
  if (c.label !== c.name) meta += ` · ${c.name}`;
  if (c.built_here) meta += " · built here";
  if (c.restarts) meta += ` · ${c.restarts} restarts`;
  setText(t.children[3], meta);
  const [cpuBtn, memBtn, gpuBtn] = t.children[4].children;
  setText(cpuBtn, c.cpu == null ? "– CPU" : `${c.cpu.toFixed(1)}% CPU`);
  cpuBtn.classList.toggle("busy", busy(c.cpu));
  setText(memBtn, c.mem == null ? "– mem" : fmtBytes(c.mem, 0));
  gpuBtn.hidden = c.gpu == null;
  if (c.gpu != null) setText(gpuBtn, `${c.gpu.toFixed(1)}% GPU`);
  gpuBtn.classList.toggle("busy", busy(c.gpu));
}

// Lit when the figure on the button isn't 0.0.
function busy(v) {
  return v != null && v.toFixed(1) !== "0.0";
}

function renderServices(list, err) {
  const errEl = $("docker-err");
  errEl.hidden = !err;
  if (err) setText(errEl, `Docker unavailable: ${err}`);
  if (!list) return;
  const box = $("tiles");
  const existing = new Map([...box.children].map((t) => [t.dataset.name, t]));
  const now = state.live.now;
  let up = 0;
  list.forEach((c, i) => {
    let t = existing.get(c.name);
    if (!t) t = makeTile(c);
    existing.delete(c.name);
    updateTile(t, c, now);
    if (box.children[i] !== t) box.insertBefore(t, box.children[i] || null);
    if (c.state === "running") up++;
  });
  existing.forEach((t) => t.remove());
  setText($("svc-count"), `${up} of ${list.length} running`);
}

// --- graph dialog ----------------------------------------------------------

const graph = { series: null, label: "", unit: "", hours: 24 };

function openGraph(btn) {
  graph.series = btn.dataset.series;
  graph.label = btn.dataset.label || graph.series;
  graph.unit = btn.dataset.unit || "";
  setText($("graph-title"), graph.label);
  $("graph-dlg").showModal();
  loadGraph();
}

async function loadGraph() {
  document.querySelectorAll("#graph-range button").forEach((b) =>
    b.classList.toggle("on", Number(b.dataset.hours) === graph.hours));
  const svg = $("graph-svg");
  svg.replaceChildren();
  try {
    const r = await fetch(`/api/history?series=${encodeURIComponent(graph.series)}&hours=${graph.hours}`, { cache: "no-store" });
    const data = await r.json();
    drawGraph(svg, data.points || []);
  } catch (e) {
    drawGraph(svg, []);
  }
}

function el(name, attrs, text) {
  const e = document.createElementNS(SVGNS, name);
  for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
  if (text != null) e.textContent = text;
  return e;
}

function drawGraph(svg, pts) {
  const W = 1600, H = 520, L = 90, R = 20, T = 20, B = 44;
  const stats = $("graph-stats");
  stats.replaceChildren();
  if (pts.length < 2) {
    svg.append(el("text", { x: W / 2, y: H / 2, "text-anchor": "middle", class: "empty" },
      "Not enough history yet.  Readings are kept every 10 seconds for 24 hours."));
    return;
  }
  const vals = pts.map((p) => p[1]);
  const lo = Math.min(...vals), hi = Math.max(...vals);
  const avg = vals.reduce((a, b) => a + b, 0) / vals.length;
  for (const [k, v] of [["Now", vals[vals.length - 1]], ["Average", avg], ["Lowest", lo], ["Highest", hi]]) {
    const s = document.createElement("span");
    s.append(`${k} `);
    const b = document.createElement("b");
    b.textContent = fmtUnit(v, graph.unit);
    s.append(b);
    stats.append(s);
  }
  let yMax = hi, yMin = Math.min(0, lo);
  if (graph.unit === "%") yMax = Math.max(100, hi);
  if (yMax === yMin) yMax = yMin + 1;
  yMax *= graph.unit === "%" ? 1 : 1.1;
  const t0 = pts[0][0], t1 = pts[pts.length - 1][0];
  const x = (t) => L + ((t - t0) / Math.max(1, t1 - t0)) * (W - L - R);
  const y = (v) => T + (1 - (v - yMin) / (yMax - yMin)) * (H - T - B);

  for (let i = 0; i <= 4; i++) {
    const v = yMin + ((yMax - yMin) * i) / 4;
    svg.append(el("line", { x1: L, x2: W - R, y1: y(v), y2: y(v), class: "grid" }));
    svg.append(el("text", { x: L - 10, y: y(v) + 5, "text-anchor": "end", class: "axis" }, fmtUnit(v, graph.unit)));
  }
  const ticks = 6;
  for (let i = 0; i <= ticks; i++) {
    const t = t0 + ((t1 - t0) * i) / ticks;
    const label = new Date(t * 1000).toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" });
    svg.append(el("text", { x: x(t), y: H - 14, "text-anchor": i === 0 ? "start" : i === ticks ? "end" : "middle", class: "axis" }, label));
  }
  const d = pts.map((p, i) => `${i ? "L" : "M"}${x(p[0]).toFixed(1)},${y(p[1]).toFixed(1)}`).join("");
  svg.append(el("path", { d: `${d}L${x(t1)},${y(yMin)}L${x(t0)},${y(yMin)}Z`, class: "area" }));
  svg.append(el("path", { d, class: "line" }));

  const cross = el("line", { y1: T, y2: H - B, class: "cross", visibility: "hidden" });
  const dot = el("circle", { r: 5, class: "dot", visibility: "hidden" });
  const tip = el("text", { class: "tip", visibility: "hidden" });
  svg.append(cross, dot, tip);
  svg.onmousemove = (ev) => {
    const box = svg.getBoundingClientRect();
    const sx = ((ev.clientX - box.left) / box.width) * W;
    const t = t0 + ((sx - L) / (W - L - R)) * (t1 - t0);
    let best = pts[0];
    for (const p of pts) if (Math.abs(p[0] - t) < Math.abs(best[0] - t)) best = p;
    const px = x(best[0]), py = y(best[1]);
    for (const e of [cross, dot, tip]) e.setAttribute("visibility", "visible");
    cross.setAttribute("x1", px); cross.setAttribute("x2", px);
    dot.setAttribute("cx", px); dot.setAttribute("cy", py);
    const when = new Date(best[0] * 1000).toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" });
    tip.textContent = `${fmtUnit(best[1], graph.unit)}  at ${when}`;
    const right = px > W - 320;
    tip.setAttribute("x", right ? px - 12 : px + 12);
    tip.setAttribute("text-anchor", right ? "end" : "start");
    tip.setAttribute("y", Math.max(T + 20, py - 12));
  };
  svg.onmouseleave = () => { for (const e of [cross, dot, tip]) e.setAttribute("visibility", "hidden"); };
}

// --- login -----------------------------------------------------------------

function setLogin(on) {
  const changed = state.loggedIn !== on;
  state.loggedIn = on;
  setText($("login-btn"), on ? "Log out" : "Log in");
  if (changed && window.BB.jobsLoginChanged) window.BB.jobsLoginChanged(on);
}

window.BB.post = post;
window.BB.isLoggedIn = () => state.loggedIn === true;
async function post(path, body) {
  const r = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Battle-Bridge": "1" },
    body: JSON.stringify(body || {}),
  });
  let data = {};
  try { data = await r.json(); } catch (e) { /* empty body */ }
  return { ok: r.ok, data };
}

async function loginSubmit(ev) {
  ev.preventDefault();
  const err = $("login-err");
  err.hidden = true;
  const { ok, data } = await post("/api/login", { password: $("login-pw").value });
  $("login-pw").value = "";
  if (ok) {
    $("login-dlg").close();
    setLogin(true);
  } else {
    err.hidden = false;
    setText(err, data.error || "Login failed.");
  }
}

// --- wiring ----------------------------------------------------------------

document.addEventListener("click", (ev) => {
  const fig = ev.target.closest("[data-series]");
  if (fig) { openGraph(fig); return; }
  const close = ev.target.closest("[data-close]");
  if (close) { close.closest("dialog").close(); return; }
  const range = ev.target.closest("#graph-range button");
  if (range) { graph.hours = Number(range.dataset.hours); loadGraph(); }
});

// Enter never submits anything except the login form.
document.addEventListener("keydown", (ev) => {
  if (ev.key === "Enter" && ev.target.matches("input, select") && !ev.target.closest("#login-form")) {
    ev.preventDefault();
  }
});

$("login-btn").addEventListener("click", async () => {
  if (state.loggedIn) {
    await post("/api/logout");
    setLogin(false);
  } else {
    $("login-err").hidden = true;
    $("login-dlg").showModal();
    $("login-pw").focus();
  }
});
$("login-form").addEventListener("submit", loginSubmit);

poll();
