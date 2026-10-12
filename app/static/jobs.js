// The Engineering drawer: fixed jobs, a live terminal, and run history.
// The page only ever asks for a job by name (and, for some, one compose
// project or container from the lists the runner itself provides).  It
// can't run anything else.
"use strict";

(function () {
  const BB = window.BB;
  const $ = (id) => document.getElementById(id);
  const J = { jobs: [], services: [], containers: [], problems: [], current: null, history: [], viewing: null,
              offset: 0, es: null, term: null, fit: null, open: false, tab: "jobs", pollTimer: null, svc: "", ctr: "" };

  // --- terminal ----------------------------------------------------------------

  function ensureTerm() {
    if (J.term) return;
    J.term = new window.Terminal({
      fontFamily: 'ui-monospace, "JetBrains Mono", "Fira Code", "DejaVu Sans Mono", monospace',
      fontSize: 15, scrollback: 20000, cursorBlink: true, allowProposedApi: false,
      theme: {
        background: "#0b0d11", foreground: "#eceff4", cursor: "#FDD50F", selectionBackground: "#323a47",
        black: "#1d222b", red: "#FF6B5E", green: "#6EC9D7", yellow: "#EAAC18", blue: "#87B7DD",
        magenta: "#B48CC8", cyan: "#6EC9D7", white: "#B3B6B6", brightBlack: "#5d6678", brightRed: "#ff8a80",
        brightGreen: "#8fdcea", brightYellow: "#FDD50F", brightBlue: "#a9cdec", brightMagenta: "#c9a6da",
        brightCyan: "#8fdcea", brightWhite: "#ffffff",
      },
    });
    J.term.loadAddon(new window.CanvasAddon.CanvasAddon());
    J.fit = new window.FitAddon.FitAddon();
    J.term.loadAddon(J.fit);
    J.term.open($("term"));
    J.term.onData((d) => {
      if (J.current && J.viewing === J.current.id && !J.current.finished) {
        BB.post("/api/jobs/input", { id: J.viewing, data: d });
      }
    });
    window.addEventListener("resize", fitTerm);
  }

  function fitTerm() {
    if (!J.term || !J.open) return;
    try { J.fit.fit(); } catch (e) { return; }
    if (J.current && J.viewing === J.current.id && !J.current.finished) {
      BB.post("/api/jobs/resize", { id: J.viewing, rows: J.term.rows, cols: J.term.cols });
    }
  }

  function b64bytes(s) {
    const bin = atob(s);
    const out = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
    return out;
  }

  // --- streaming -----------------------------------------------------------------

  // Shows a finished log (a PC job's) in the terminal.  No reconnecting.
  function showLog(url, title) {
    if (!J.term && !J.settled) { setTimeout(() => showLog(url, title), 400); return; }
    ensureTerm();
    showTerm();
    if (J.es) { J.es.close(); J.es = null; }
    J.viewing = null;
    J.term.reset();
    // PC job logs are written with plain line feeds (tee), not a terminal's
    // CR LF, so each new line must also go back to the start.
    J.term.options.convertEol = true;
    $("term-title").textContent = title;
    setStatus("", "");
    const es = new EventSource(url);
    J.es = es;
    es.addEventListener("out", (ev) => J.term.write(b64bytes(ev.data)));
    es.addEventListener("end", () => { es.close(); if (J.es === es) J.es = null; });
    es.addEventListener("fail", (ev) => setStatus(JSON.parse(ev.data), "bad"));
    es.onerror = () => { es.close(); if (J.es === es) J.es = null; };
  }
  BB.showLog = showLog;

  function showTerm() {
    $("steps").hidden = true;
    $("term").hidden = false;
    $("term-foot").hidden = false;
  }
  BB.showTerm = showTerm;

  function attach(id, label) {
    if (!J.term && !J.settled) { setTimeout(() => attach(id, label), 400); return; }
    ensureTerm();
    showTerm();
    if (J.es) { J.es.close(); J.es = null; }
    J.viewing = id;
    J.offset = 0;
    J.term.reset();
    J.term.options.convertEol = false;
    $("term-title").textContent = label || `Job ${id}`;
    setStatus("connecting…", "");
    connect();
    renderHistory();
  }

  function connect() {
    const id = J.viewing;
    const es = new EventSource(`/api/jobs/stream?id=${id}&offset=${J.offset}`);
    J.es = es;
    es.addEventListener("out", (ev) => {
      const bytes = b64bytes(ev.data);
      J.offset += bytes.length;
      J.term.write(bytes);
      setStatus("running", "run");
    });
    es.addEventListener("end", (ev) => {
      es.close();
      if (J.es === es) J.es = null;
      const meta = JSON.parse(ev.data || "null");
      if (meta && meta.finished) setStatus(meta.exit === 0 ? "finished" : `finished, exit ${meta.exit}`, meta.exit === 0 ? "" : "bad");
      else setStatus("finished", "");
      refresh();
    });
    es.addEventListener("fail", (ev) => {
      setStatus(JSON.parse(ev.data), "bad");
    });
    es.onerror = () => {
      // Battle Bridge itself may be restarting (a Docker update does that).
      // The job carries on in the runner; pick up where we left off.
      es.close();
      if (J.es !== es) return;
      J.es = null;
      setStatus("reconnecting…", "bad");
      setTimeout(() => { if (J.viewing === id && !J.es) connect(); }, 3000);
    };
  }

  function setStatus(text, cls) {
    const s = $("term-status");
    s.textContent = text;
    s.className = cls || "";
    const live = J.current && !J.current.finished && J.viewing === J.current.id;
    $("term-stop").hidden = !(live && (cls === "run" || text === "connecting…"));
  }

  // --- data ------------------------------------------------------------------------

  async function refresh() {
    const r = await fetch("/api/jobs", { cache: "no-store" });
    const data = await r.json().catch(() => ({}));
    const rs = $("runner-state");
    if (r.status === 401) { showLoggedOut(); return; }
    if (!r.ok) {
      rs.textContent = data.error || "The job runner isn't answering.";
      rs.className = "runner-state bad";
      return;
    }
    rs.textContent = "Runner connected";
    rs.className = "runner-state";
    const firstLoad = !J.jobs.length;
    J.jobs = data.jobs || [];
    J.services = (data.services || []).filter((s) => !s.missing);
    J.containers = data.containers || [];
    J.problems = data.problems || [];
    J.current = data.current;
    J.history = data.history || [];
    const busy = J.current && !J.current.finished;
    $("drawer-busy").hidden = !busy;
    if (firstLoad || J.jobsSig !== JSON.stringify([J.jobs, J.services, J.containers, J.problems])) renderJobs();
    setButtonsBusy(busy);
    renderHistory();
    // Show a job that's just started.  Only a new one: someone reading an
    // older run or a PC log while a job runs keeps what they're reading.
    if (J.open && busy && J.current.id !== J.lastStarted) {
      J.lastStarted = J.current.id;
      if (J.viewing !== J.current.id) attach(J.current.id, J.current.label);
    }
  }

  function showLoggedOut() {
    $("jobs-pane").replaceChildren(BB.h("p", { class: "pending" }, "Log in to use the job runner."),
      BB.h("button", { type: "button", class: "btn primary", id: "drawer-login" }, "Log in"));
    $("history-pane").replaceChildren();
    J.histSig = null;
    $("runner-state").textContent = "";
    J.jobs = [];
  }

  // --- rendering -------------------------------------------------------------------

  function renderJobs() {
    const h = BB.h;
    J.jobsSig = JSON.stringify([J.jobs, J.services, J.containers, J.problems]);
    const pane = $("jobs-pane");
    const svcNames = J.services.map((s) => s.folder);
    if (!svcNames.includes(J.svc)) J.svc = svcNames[0] || "";
    if (!J.containers.includes(J.ctr)) J.ctr = J.containers[0] || "";
    const sel = h("select", { id: "svc-select", "aria-label": "Compose project" },
      J.services.map((s) => h("option", { value: s.folder, selected: s.folder === J.svc || null },
        s.folder + (s.containers.length ? `  (${s.containers.join(", ")})` : "") + (s.built_here ? "  · built here" : ""))));
    sel.addEventListener("input", () => { J.svc = sel.value; setButtonsBusy(J.current && !J.current.finished); });
    const csel = h("select", { id: "ctr-select", "aria-label": "Container" },
      J.containers.map((n) => h("option", { value: n, selected: n === J.ctr || null }, n)));
    csel.addEventListener("input", () => { J.ctr = csel.value; });

    const out = [];
    const projectJobs = J.jobs.filter((j) => j.arg === "project" || j.arg === "built");
    if (J.services.length) {
      out.push(h("div", { class: "jgroup" }, h("h3", {}, "One compose project"),
        h("div", { class: "svcpick" }, sel),
        h("div", { class: "jbtns" }, projectJobs.map((j) => jobButton(j)))));
    } else {
      out.push(h("div", { class: "jgroup" }, h("h3", {}, "One compose project"),
        h("p", { class: "pc-note" }, "No compose projects found.  Containers started with docker compose show up here.")));
    }
    const ctrJobs = J.jobs.filter((j) => j.arg === "container");
    if (J.containers.length && ctrJobs.length) {
      out.push(h("div", { class: "jgroup" }, h("h3", {}, "One container"),
        h("div", { class: "svcpick" }, csel, ctrJobs.map((j) => jobButton(j)))));
    }
    const groups = {};
    for (const j of J.jobs) if (!j.arg) (groups[j.group] = groups[j.group] || []).push(j);
    for (const [g, list] of Object.entries(groups)) {
      out.push(h("div", { class: "jgroup" }, h("h3", {}, g), h("div", { class: "jbtns" }, list.map((j) => jobButton(j)))));
    }
    for (const p of J.problems) out.push(h("p", { class: "err" }, p));
    out.push(pcSection());
    pane.replaceChildren(...out);
    if (BB.renderPc) BB.renderPc($("pc-btns"));
  }

  function jobButton(j) {
    const text = j.label.replace(/^(Read|Follow) a project's logs/, (m, w) => (w === "Read" ? "Logs" : "Follow logs"))
      .replace(" live", " live (Stop ends it)")
      .replace(/ (a|one) (built-here )?(project|container)$/, "");
    return BB.h("button", { type: "button", class: `btn ${j.changes ? "change" : ""}`, "data-job": j.name, "data-arg": j.arg || "" }, text);
  }

  function setButtonsBusy(busy) {
    const built = new Set(J.services.filter((s) => s.built_here).map((s) => s.folder));
    document.querySelectorAll("#jobs-pane [data-job]").forEach((b) => {
      const needsBuilt = b.dataset.arg === "built" && !built.has(J.svc);
      b.disabled = !!busy || needsBuilt;
      b.title = busy ? "A job is running.  One at a time." : needsBuilt ? "Only for projects built here." : "";
    });
  }

  function pcSection() {
    const h = BB.h;
    return h("div", { class: "jgroup", id: "pc-jobs" }, h("h3", {}, "On your PC (opens a terminal)"),
      h("p", { class: "pc-note" }, "These need sudo, so they run in a terminal on your PC over SSH, never in this page."),
      h("div", { class: "jbtns", id: "pc-btns" }));
  }

  function renderHistory() {
    const h = BB.h;
    const pane = $("history-pane");
    // Rebuilt only when something changed, so a click on a run isn't lost
    // to a redraw.
    const pcItems = ((((BB.lastLive || {}).facts) || {}).pc_jobs || {}).items || [];
    const sig = JSON.stringify([J.history, J.viewing, pcItems]);
    if (sig === J.histSig) return;
    J.histSig = sig;
    const pcPart = BB.pcHistory ? BB.pcHistory() : [];
    if (!J.history.length) { pane.replaceChildren(h("p", { class: "pending" }, "No runs yet."), ...pcPart); return; }
    pane.replaceChildren(h("h3", { class: "hist-sub" }, "In this page"), h("ul", { class: "hist" }, J.history.map((r) => {
      const running = !r.finished;
      const exit = running ? "running" : r.interrupted ? "interrupted" : r.exit === 0 ? "ok" : `exit ${r.exit}`;
      const cls = running ? "v-ok" : r.exit === 0 ? "v-ok" : "v-bad";
      return h("li", {}, h("button", { type: "button", class: r.id === J.viewing ? "on" : null, "data-run": r.id },
        h("span", {}, r.label), h("span", { class: "hwhen" }, BB.when(r.started)), h("span", { class: `hexit ${cls}` }, exit)));
    })), ...pcPart);
  }

  // --- actions ---------------------------------------------------------------------

  async function startJob(name, arg) {
    const job = J.jobs.find((j) => j.name === name);
    if (!job) return;
    const a = job.arg === "container" ? J.ctr : job.arg ? J.svc : null;
    if (job.changes) {
      $("confirm-text").textContent = (job.confirm || "Run this job?").replace("{arg}", a || "");
      $("confirm-yes").dataset.job = name;
      $("confirm-yes").dataset.arg = a || "";
      $("confirm-dlg").showModal();
      return;
    }
    run(name, a);
  }

  async function run(name, arg) {
    const { ok, data } = await BB.post("/api/jobs/start", { job: name, arg: arg || null });
    if (!ok) {
      setStatus(data.error || "Couldn't start that.", "bad");
      if (data.running) attach(data.running);
      return;
    }
    const job = J.jobs.find((j) => j.name === name);
    attach(data.id, job.label + (arg ? `: ${arg}` : ""));
    setTimeout(fitTerm, 50);
    refresh();
  }

  function openDrawer() {
    J.open = true;
    $("drawer").classList.add("open");
    // Make the terminal only once the drawer has finished sliding open, so
    // xterm measures its characters in a settled, visible box.
    setTimeout(() => { J.settled = true; ensureTerm(); fitTerm(); }, 350);
    clearInterval(J.pollTimer);
    if (!BB.isLoggedIn()) { showLoggedOut(); return; }
    refresh();
    J.pollTimer = setInterval(refresh, 5000);
  }
  function closeDrawer() {
    J.open = false;
    $("drawer").classList.remove("open");
    clearInterval(J.pollTimer);
  }
  BB.openDrawer = openDrawer;

  // The top bar's "Update available": open the drawer and ask to update
  // the project Battle Bridge's own container belongs to.
  async function updateSelf(container) {
    openDrawer();
    setTab("jobs");
    if (!BB.isLoggedIn()) return;
    await refresh();
    const p = J.services.find((s) => s.containers.includes(container));
    if (!p) {
      setStatus(`Couldn't find the compose folder for ${container}.  Use Update one project.`, "bad");
      return;
    }
    const job = J.jobs.find((j) => j.name === "update-one");
    if (!job) return;
    $("confirm-text").textContent = job.confirm.replace("{arg}", p.folder) +
      "  This page will drop out for a few seconds while it restarts.";
    $("confirm-yes").dataset.job = "update-one";
    $("confirm-yes").dataset.arg = p.folder;
    $("confirm-dlg").showModal();
  }

  function setTab(t) {
    J.tab = t;
    document.querySelectorAll(".tabs [data-tab]").forEach((b) => b.classList.toggle("on", b.dataset.tab === t));
    $("jobs-pane").hidden = t !== "jobs";
    $("history-pane").hidden = t !== "history";
  }

  document.addEventListener("click", (ev) => {
    const t = ev.target;
    if (t.closest("#drawer-btn")) { J.open ? closeDrawer() : openDrawer(); return; }
    if (t.closest("#drawer-close")) { closeDrawer(); return; }
    const ul = t.closest("#update-light");
    if (ul) { updateSelf(ul.dataset.container); return; }
    if (t.closest("#drawer-login")) { document.getElementById("login-btn").click(); return; }
    const tab = t.closest(".tabs [data-tab]");
    if (tab) { setTab(tab.dataset.tab); return; }
    const jb = t.closest("#jobs-pane [data-job]");
    if (jb && !jb.disabled) { startJob(jb.dataset.job); return; }
    const hr = t.closest("[data-run]");
    if (hr) {
      const r = J.history.find((x) => x.id === Number(hr.dataset.run));
      attach(Number(hr.dataset.run), r ? r.label : null);
      return;
    }
    if (t.closest("#term-stop")) {
      if (J.viewing != null) BB.post("/api/jobs/stop", { id: J.viewing });
      setStatus("stopping…", "");
      return;
    }
    if (t.closest("#confirm-yes")) {
      const b = $("confirm-yes");
      $("confirm-dlg").close();
      run(b.dataset.job, b.dataset.arg || null);
    }
  });
  document.addEventListener("keydown", (ev) => {
    if (ev.key === "Escape" && J.open && !document.querySelector("dialog[open]")) closeDrawer();
  });

  // Called by app.js when the login state changes.
  BB.jobsLoginChanged = (on) => {
    if (on && J.open) { refresh(); clearInterval(J.pollTimer); J.pollTimer = setInterval(refresh, 5000); }
    if (!on) { $("drawer-busy").hidden = true; if (J.open) showLoggedOut(); }
  };
  // A light check so the tab can say a job is running, even with the drawer shut.
  setInterval(() => { if (!J.open && BB.isLoggedIn()) refresh(); }, 15000);
})();
