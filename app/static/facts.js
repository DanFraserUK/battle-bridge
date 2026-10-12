// Boxes fed by the host runner's facts: drives, updates, scheduled jobs,
// the certificates part of the network box, and the reboot light.
// Each box is rebuilt only when its own data changes, so links and buttons
// inside it are never swapped out from under a click.
"use strict";

const BB = window.BB || (window.BB = {});

function h(tag, attrs, ...kids) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k === "class") e.className = v;
    else e.setAttribute(k, v === true ? "" : v);
  }
  for (const k of kids.flat(Infinity)) {
    if (k == null || k === false) continue;
    e.append(k instanceof Node ? k : document.createTextNode(String(k)));
  }
  return e;
}
BB.h = h;

// A redraw between mouse-down and mouse-up loses the click.  So nothing is
// rebuilt while a button is held; it catches up on the next poll.
let pointerDown = false;
document.addEventListener("pointerdown", () => { pointerDown = true; }, true);
document.addEventListener("pointerup", () => { setTimeout(() => { pointerDown = false; }, 0); }, true);
document.addEventListener("pointercancel", () => { pointerDown = false; }, true);

const lastSig = {};
function rebuild(id, data, build) {
  const sig = JSON.stringify(data, (k, v) => (k === "checked" || k === "took" || k === "written" ? undefined : v));
  if (lastSig[id] === sig || pointerDown) return;
  lastSig[id] = sig;
  const box = document.getElementById(id);
  box.replaceChildren(...[].concat(build(data)).filter(Boolean));
}

// --- time ------------------------------------------------------------------

function ago(t, now) {
  if (!t) return "never";
  const s = now - t;
  const fut = s < 0;
  const a = Math.abs(s);
  let txt;
  if (a < 90) txt = "just now";
  else if (a < 3600) txt = `${Math.round(a / 60)} min`;
  else if (a < 86400 * 2) txt = `${Math.round(a / 3600)} h`;
  else txt = `${Math.round(a / 86400)} days`;
  if (txt === "just now") return txt;
  return fut ? `in ${txt}` : `${txt} ago`;
}
function when(t) {
  if (!t) return "";
  return new Date(t * 1000).toLocaleString("en-GB", { weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
}
function day(t) {
  return t ? new Date(t * 1000).toLocaleDateString("en-GB", { day: "numeric", month: "short" }) : "";
}
function every(sec) {
  if (sec % 86400 === 0) return sec === 86400 ? "daily" : `every ${sec / 86400} days`;
  if (sec % 3600 === 0) return sec === 3600 ? "hourly" : `every ${sec / 3600} h`;
  return `every ${Math.round(sec / 60)} min`;
}
BB.ago = ago;

// --- small pieces ----------------------------------------------------------

function mark(kind) {
  const sym = { ok: "✓", bad: "✕", warn: "!", info: "·" }[kind] || "·";
  return h("span", { class: `mark m-${kind}`, "aria-label": kind }, sym);
}
function row(kind, label, value, note) {
  return h("div", { class: "frow" }, mark(kind), h("span", { class: "flabel" }, label),
    h("span", { class: `fval v-${kind}` }, value), note ? h("span", { class: "fnote" }, note) : null);
}
function problem(text) { return h("p", { class: "err" }, text); }
function pending(text) { return h("p", { class: "pending" }, text); }
function svgBar(pct, cls) {
  const ns = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(ns, "svg");
  svg.setAttribute("class", "bar");
  svg.setAttribute("viewBox", "0 0 100 6");
  svg.setAttribute("preserveAspectRatio", "none");
  const bg = document.createElementNS(ns, "rect");
  bg.setAttribute("class", "bar-bg"); bg.setAttribute("width", "100"); bg.setAttribute("height", "6");
  const fg = document.createElementNS(ns, "rect");
  fg.setAttribute("class", `bar-fg ${cls || ""}`.trim());
  fg.setAttribute("width", String(Math.max(0, Math.min(100, pct || 0)).toFixed(1)));
  fg.setAttribute("height", "6");
  svg.append(bg, fg);
  return svg;
}
function size(n) {
  if (n == null) return "–";
  const u = ["B", "KiB", "MiB", "GiB", "TiB"];
  let i = 0;
  while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
  return `${n.toFixed(n < 10 && i > 2 ? 2 : i > 2 ? 1 : 0)} ${u[i]}`;
}
function errOf(x) { return x && x.error ? x.error : null; }

// --- drives ----------------------------------------------------------------

// The SMART entry for a block device: sda for sda, nvme0 for nvme0n1.
function smartFor(smart, disk) {
  return smart.find((s) => s.name === disk || disk.startsWith(`${s.name}n`)) || null;
}
function smartKind(s) {
  if (s.ok === false) return "bad";
  if (s.ok == null) return "info";
  return s.notes && s.notes.length ? "warn" : "ok";
}

function drives(f, now) {
  const out = [];
  const root = f.root || {};
  const smart = root.smart || [];
  const btrfs = new Map((root.btrfs || []).map((b) => [b.mount, b]));
  for (const d of (f.disks || {}).items || []) {
    const pctCls = d.pct >= 95 ? "hot" : d.pct >= 90 ? "warn" : "";
    const mine = (d.disks || []).map((n) => smartFor(smart, n)).filter(Boolean);
    let health = null;
    if (mine.length) {
      const kinds = mine.map(smartKind);
      const kind = kinds.includes("bad") ? "bad" : kinds.includes("warn") ? "warn" : kinds.includes("ok") ? "ok" : "info";
      const temps = mine.map((s) => s.temp).filter((t) => t != null);
      health = h("span", { class: `dhealth v-${kind}` }, mark(kind),
        kind === "bad" ? "failing" : temps.length ? temps.map((t) => `${t}°C`).join(" ") : kind === "info" ? "no SMART" : "OK");
    }
    const b = btrfs.get(d.mount);
    out.push(h("div", { class: "drive" },
      h("div", { class: "dhead" }, h("b", {}, d.label), h("span", { class: "dmount" },
        d.mount, d.disks && d.disks.length ? ` · ${d.disks.join(" ")}` : ""), health),
      d.error ? problem(d.error) : !d.mounted ? problem("Not mounted") : [
        svgBar(d.pct, pctCls),
        h("div", { class: "dnums" }, h("span", { class: pctCls }, `${d.pct}% used`),
          h("span", {}, `${size(d.free)} free of ${size(d.total)}`)),
      ],
      b ? btrfsRows(b) : null));
  }
  if (errOf(f.disks)) out.push(problem(`Drives: ${f.disks.error}`));
  if (errOf(root)) out.push(problem(`Drive health: ${root.error}`));
  for (const n of root.notes || []) out.push(pending(n));
  for (const p of root.zfs || []) {
    out.push(row(p.ok ? "ok" : "bad", `ZFS pool ${p.name}`, p.health, `${p.alloc} used of ${p.size} (${p.cap})`));
  }
  if (smart.length) {
    out.push(h("h3", { class: "minor" }, "Drive health"));
    for (const s of smart) {
      const kind = smartKind(s);
      const bits = [];
      if (s.hours != null) bits.push(`${Math.round(s.hours / 24 / 365 * 10) / 10} years on`);
      if (s.wear != null) bits.push(`${s.wear}% worn`);
      bits.push(...(s.notes || []));
      if (s.error) bits.push(s.error);
      out.push(row(kind, `${s.name}${s.model ? ` · ${s.model}` : ""}`,
        s.ok === false ? "FAILING" : s.ok == null ? "unknown" : s.temp != null ? `${s.temp}°C` : "OK", bits.join(" · ") || null));
    }
  }
  const smartd = (f.smartd || {}).state;
  if (smartd) out.push(row(smartd === "active" ? "ok" : "warn", "smartd", smartd,
    smartd === "active" ? null : "No scheduled self-tests while it's stopped"));
  if (root.checked) out.push(h("p", { class: "checked" }, `Health checked ${ago(root.checked, now)}`));
  return out;
}

function btrfsRows(b) {
  const bad = (b.devices || []).filter((d) => d.total > 0);
  const out = [];
  if ((b.devices || []).length > 1 || bad.length) {
    out.push(row(bad.length ? "bad" : "ok", "btrfs error counters",
      bad.length ? bad.map((d) => `${d.device}: ${d.total}`).join(", ") : "all zero",
      bad.length ? "Non-zero on one device of a mirror means it's failing." : null));
  }
  const sc = b.scrub;
  if (sc) {
    if (sc.never) out.push(row("info", "Last scrub", "never run"));
    else out.push(row(sc.ok ? "ok" : "bad", "Last scrub", `${sc.status || "?"} · ${sc.errors || "?"}`, sc.started || null));
  }
  return out;
}

// --- updates ---------------------------------------------------------------

function listLine(items, fmt, max = 6) {
  if (!items.length) return null;
  const shown = items.slice(0, max).map(fmt).join(", ");
  return h("div", { class: "ulist" }, shown + (items.length > max ? `, and ${items.length - max} more` : ""));
}

const PKG = { arch: "System packages", debian: "System packages (apt)", fedora: "System packages (dnf)" };

function updates(f, now) {
  const out = [];
  const k = f.reboot || {};
  if (k.needed) out.push(row("warn", "Reboot needed", k.reasons.join("; ")));
  const up = f.updates || {}, img = f.image_updates || {};
  const label = PKG[up.family] || "System packages";
  if (errOf(up)) out.push(row("bad", label, "check failed", up.error));
  else if (up.items) {
    out.push(row(up.items.length ? "warn" : "ok", label, up.items.length ? `${up.items.length} waiting` : "up to date"));
    out.push(listLine(up.items, (p) => p.name, 8));
  }
  const aur = up.aur;
  if (aur && errOf(aur)) out.push(row("bad", "AUR", "check failed", aur.error));
  else if (aur && aur.items) {
    out.push(row(aur.items.length ? "warn" : "ok", "AUR", aur.items.length ? `${aur.items.length} waiting` : "up to date"));
    out.push(listLine(aur.items, (p) => p.name));
  }
  if (errOf(img)) out.push(row("bad", "Images", "check failed", img.error));
  else if (img.items) {
    const newer = img.items.filter((i) => i.status === "newer");
    const unknown = img.items.filter((i) => i.status === "unknown");
    out.push(row(newer.length ? "warn" : unknown.length ? "info" : "ok", "Images",
      newer.length ? `${newer.length} newer` : "all current",
      unknown.length ? `${unknown.length} couldn't be checked` : null));
    out.push(listLine(newer, (i) => i.container));
    const built = img.items.filter((i) => i.status === "built here");
    if (built.length) out.push(row("info", "Built here", built.map((i) => i.container).join(", "), "rebuild to pick up base image fixes"));
  }
  const checked = Math.min(...[up, img].map((x) => x.checked || Infinity));
  if (isFinite(checked)) {
    if (k.booted && checked < k.booted) {
      out.push(row("warn", "Checked before the last boot", "re-checking",
        "The runner re-checks a minute after boot.  Or press Check for updates now."));
    }
    out.push(h("p", { class: "checked" }, `Checked ${ago(checked, now)}.  Rechecked after any update or reboot.  Nothing updates by itself.`));
  }
  const news = f.news || {};
  if (news.items && news.items.length) {
    out.push(h("h3", { class: "minor" }, "Arch news"));
    out.push(h("ul", { class: "news" }, news.items.slice(0, 4).map((n) =>
      h("li", { class: n.when > (now - 14 * 86400) ? "fresh" : null },
        h("span", { class: "ndate" }, day(n.when)),
        h("a", { href: n.link, target: "_blank", rel: "noopener noreferrer" }, n.title)))));
  } else if (errOf(news)) out.push(row("bad", "Arch news", "couldn't fetch", news.error));
  return out;
}

// --- scheduled jobs --------------------------------------------------------

function scheduled(f, now) {
  const out = [];
  const ch = f.checks || {};
  for (const c of ch.items || []) {
    out.push(row(c.ok ? "ok" : "bad", c.label, c.ok ? "as expected" : "needs a look",
      c.ok ? `${c.note ? `${c.note} · ` : ""}${every(c.every)}, last ${ago(c.ran, now)}` : (c.output || (c.exit === 0 ? "no output" : `exit ${c.exit}`))));
  }
  for (const p of ch.problems || []) out.push(problem(p));
  if (!(ch.items || []).length && !(ch.problems || []).length) {
    out.push(pending("Add outcome checks of your own in /etc/battle-bridge/checks.d.  See the examples folder."));
  }
  const all = ((f.timers || {}).items) || [];
  if (errOf(f.timers)) out.push(problem(`Timers: ${f.timers.error}`));
  if (all.length) {
    out.push(h("h3", { class: "minor" }, "systemd timers"));
    out.push(h("table", { class: "timers" },
      h("thead", {}, h("tr", {}, h("th", {}, ""), h("th", {}, "Timer"), h("th", {}, "Last"), h("th", {}, "Next"))),
      h("tbody", {}, all.map((t) => {
        const kind = !t.last ? "info" : t.result && t.result !== "success" ? "bad" : "ok";
        return h("tr", {},
          h("td", {}, mark(kind)),
          h("td", { title: t.result && t.result !== "success" ? `last run: ${t.result}` : null }, t.unit.replace(/\.timer$/, "")),
          h("td", {}, ago(t.last, now)),
          h("td", {}, t.next ? ago(t.next, now) : "–"));
      }))));
  }
  return out;
}

// --- certificates (in the network box) ------------------------------------

function certSection(f, now) {
  const certs = ((f.root || {}).certs) || [];
  if (!certs.length) return [];
  const sorted = [...certs].sort((a, b) => (a.expires || 0) - (b.expires || 0));
  const low = sorted.filter((c) => c.expires && (c.expires - now) / 86400 < 25);
  const out = [row(low.length ? "warn" : "ok", "Certificates",
    low.length ? `${low.length} under 25 days` : "all over 25 days",
    low.length ? "Nginx Proxy Manager renews at 30 days, so these should have renewed." : "Nginx Proxy Manager renews them itself")];
  out.push(h("div", { class: "certs" }, sorted.map((c) => {
    const days = c.expires ? Math.floor((c.expires - now) / 86400) : null;
    const cls = days == null ? "v-bad" : days < 14 ? "v-bad" : days < 25 ? "v-warn" : "";
    return h("div", { class: "cert" }, h("span", { class: "cname" }, c.domain || c.name),
      h("span", { class: `cdays ${cls}` }, days == null ? "?" : `${days} d`));
  })));
  return out;
}

// --- shared with services.js ------------------------------------------------

Object.assign(BB, { rebuild, row, mark, problem, pending, svgBar, size, when, day, certSection });

// --- entry point -----------------------------------------------------------

BB.renderFacts = function (live) {
  const f = live.facts;
  const now = live.now;
  const prob = live.facts_problem;
  const light = document.getElementById("reboot-light");
  const k = f && f.reboot;
  light.hidden = !(k && k.needed);
  if (k && k.needed) light.title = k.reasons.join("; ");
  // Battle Bridge's own image, from the runner's daily image check.
  const upd = document.getElementById("update-light");
  const items = ((f && f.image_updates) || {}).items || [];
  const self = items.find((i) => /(^|\/)battle-bridge(:|@|$)/.test(i.image) && i.status === "newer");
  upd.hidden = !self;
  upd.dataset.container = self ? self.container : "";
  if (!f) {
    for (const id of ["drives", "updates", "scheduled"]) rebuild(id, prob, () => pending(prob));
    return;
  }
  const withProblem = (fn) => (data) => (prob ? [problem(prob)] : []).concat(fn(data, now));
  rebuild("drives", { d: f.disks, r: f.root, s: f.smartd, p: prob, m: Math.floor(now / 600) }, () => withProblem(drives)(f));
  rebuild("updates", { k: f.reboot, a: f.updates, c: f.image_updates, n: f.news, p: prob, m: Math.floor(now / 600) }, () => withProblem(updates)(f));
  rebuild("scheduled", { c: f.checks, t: f.timers, p: prob, m: Math.floor(now / 60) }, () => withProblem(scheduled)(f));
};
