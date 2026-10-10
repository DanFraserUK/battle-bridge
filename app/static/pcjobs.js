// Jobs that need sudo.  These never run in the page: a battlebridge:// link
// asks the link handler on your PC to open a terminal, SSH'd into the
// server.  Then the page shows what happens next, or (for a reboot) counts
// down and keeps watch.
"use strict";

(function () {
  const BB = window.BB;
  const $ = (id) => document.getElementById(id);
  const h = (...a) => BB.h(...a);

  function openLink(name) {
    // Only these exist in the handler too.  Anything else is refused there.
    if (!/^(update-system|reboot|terminal\/(0|[1-9][0-9]?))$/.test(name)) return;
    window.location.href = `battlebridge://${name}`;
  }

  // --- the PC section in the Jobs tab -------------------------------------------

  let folderPick = 0;

  BB.renderPc = function (box) {
    const live = BB.lastLive || {};
    if (!live.pc_ready) {
      box.replaceChildren(h("p", { class: "pc-note" }, "Set these up first: pick your terminal and SSH login on the ",
        h("a", { href: "/setup#pc" }, "setup page"), ", then install the small link handler on your PC."));
      return;
    }
    const news = (((live.facts || {}).news) || {}).items || [];
    const fresh = (n) => n.when && live.now - n.when < 14 * 86400;
    const folders = live.pc_folders || ["~"];
    if (folderPick >= folders.length) folderPick = 0;
    const sel = h("select", { id: "pc-folder", "aria-label": "Folder on the server" },
      folders.map((f, i) => h("option", { value: String(i), selected: i === folderPick || null }, f === "~" ? "home folder" : f)));
    sel.addEventListener("input", () => { folderPick = Number(sel.value); });
    box.replaceChildren(
      h("div", { class: "pcrow" },
        h("button", { type: "button", class: "btn pc", "data-pc": "update-system" }, "Update system"),
        news.length ? h("div", { class: "pcnews" }, h("ul", {}, news.slice(0, 3).map((n) =>
          h("li", { class: fresh(n) ? "fresh" : null }, h("span", {}, BB.day(n.when)),
            h("a", { href: n.link, target: "_blank", rel: "noopener noreferrer" }, n.title))))) : h("div", {})),
      h("div", { class: "svcpick" }, h("span", { class: "pc-note" }, "Terminal on the server in"), sel,
        h("button", { type: "button", class: "btn pc", "data-pc": "terminal" }, "Open")),
      h("div", { class: "jbtns" },
        h("button", { type: "button", class: "btn danger", "data-pc": "reboot" }, "Reboot server…")));
  };

  // --- what happens next, for the jobs that open a terminal ----------------------------

  const STEPS = {
    terminal: {
      title: "Terminal on the server",
      intro: "A terminal has opened on your PC, SSH'd into the server and already in that folder.",
      steps: [
        ["It's an ordinary shell, the same as typing ssh yourself.  The page has no say in what runs there.", null],
      ],
    },
    "update-system": {
      title: "Update system",
      intro: "A terminal has opened on your PC, logged in to the server.  The update script runs there.  "
        + "It asks for your sudo password, because installing packages needs root.  The password goes over SSH, never to this page.",
      steps: [
        ["It asks before changing anything.  On Arch it shows the latest news first.", null],
        ["Then the package manager runs (pacman and your AUR helper, apt, or dnf), then it says whether a reboot is needed.", null],
        ["When it's done, the log appears under History, On your PC.  The reboot light updates within a minute.", null],
      ],
    },
  };
  const TIP = "Nothing opened?  Your browser asks once whether to open battlebridge:// links.  If it never asked, "
    + "install the link handler again from the setup page.";

  function showSteps(name) {
    const s = STEPS[name];
    BB.openDrawer();
    $("term").hidden = true;
    $("term-foot").hidden = true;
    $("term-title").textContent = s.title;
    $("term-status").textContent = "in a terminal on your PC";
    $("term-status").className = "";
    const box = $("steps");
    box.hidden = false;
    box.replaceChildren(
      h("h3", {}, s.title), h("p", {}, s.intro),
      h("ol", {}, s.steps.map(([text, cmd]) => h("li", {}, text, cmd ? h("code", {}, cmd) : null))),
      h("p", { class: "tip" }, TIP),
      h("div", { class: "row" }, h("button", { type: "button", class: "btn ghost", "data-steps-close": "1" }, "Back to the terminal")));
  }

  // --- History: what the PC jobs left behind -------------------------------------------

  BB.pcHistory = function () {
    const items = ((((BB.lastLive || {}).facts) || {}).pc_jobs || {}).items || [];
    if (!items.length) return [];
    return [h("h3", { class: "hist-sub" }, "On your PC"), h("ul", { class: "hist" }, items.map((r) => {
      const res = r.ok === true ? ["ok", "v-ok"] : r.ok === false ? ["stopped or failed", "v-bad"] : ["unclear", "v-warn"];
      return h("li", {}, h("button", { type: "button", "data-pclog": r.file },
        h("span", {}, r.job === "reboot" ? "Reboot" : "Update system"), h("span", { class: "hwhen" }, BB.when(r.when)),
        h("span", { class: `hexit ${res[1]}` }, res[0])));
    }))];
  };

  // --- reboot: confirm, count down, open the terminal, keep watch, then check -----------

  const R = { timer: null, phase: null, startBoot: null, watch: null, seenDown: false };

  function bootTime(live) {
    return live && live.host ? live.now - live.host.uptime : null;
  }

  function rebootBody(...kids) { $("reboot-body").replaceChildren(...kids); }
  function rebootButtons(...kids) { $("reboot-buttons").replaceChildren(...kids); }

  function rebootAsk() {
    R.phase = "ask";
    const name = (BB.lastLive || {}).hostname;
    $("reboot-title").textContent = name ? `Reboot ${name}` : "Reboot the server";
    rebootBody(h("p", { class: "phase" }, "Reboot the server now?"),
      h("p", { class: "sub" }, "Every service stops for a couple of minutes.  Downloads and streams drop."));
    rebootButtons(h("button", { type: "button", class: "btn danger", "data-reboot": "go" }, "Yes, reboot"),
      h("button", { type: "button", class: "btn ghost", "data-reboot": "cancel" }, "Cancel"));
    $("reboot-dlg").showModal();
  }

  function rebootCountdown() {
    R.phase = "count";
    let n = 5;
    const num = h("div", { class: "count" }, String(n));
    rebootBody(h("p", { class: "phase" }, "Rebooting in"), num);
    rebootButtons(h("button", { type: "button", class: "btn primary big", "data-reboot": "cancel" }, "Cancel"));
    R.timer = setInterval(() => {
      n -= 1;
      num.textContent = String(n);
      if (n <= 0) {
        clearInterval(R.timer);
        R.timer = null;
        rebootFire();
      }
    }, 1000);
  }

  function rebootFire() {
    R.phase = "terminal";
    R.startBoot = bootTime(BB.lastLive);
    R.seenDown = false;
    openLink("reboot");
    rebootBody(h("p", { class: "phase wait" }, "A terminal has opened on your PC."),
      h("p", { class: "sub" }, "Rebooting needs root, so it asks for your sudo password there.  "
        + "Enter it to reboot, or press Ctrl+C to cancel.  The password goes over SSH, never to this page."));
    rebootButtons(h("button", { type: "button", class: "btn ghost", "data-reboot": "close" }, "Stop watching"));
    clearInterval(R.watch);
    R.watch = setInterval(rebootWatch, 3000);
  }

  async function rebootWatch() {
    let live = null;
    try {
      const r = await fetch("/api/live", { cache: "no-store" });
      if (r.ok) live = await r.json();
    } catch (e) { live = null; }
    if (!live) {
      if (!R.seenDown) {
        R.seenDown = true;
        R.phase = "down";
        rebootBody(h("p", { class: "phase wait" }, "Server restarting…"),
          h("p", { class: "sub" }, "Checking every few seconds.  This page will carry on by itself."));
      }
      return;
    }
    const boot = bootTime(live);
    if (boot && R.startBoot && boot > R.startBoot + 30) {
      clearInterval(R.watch);
      R.watch = null;
      rebootChecks();
    }
  }

  async function rebootChecks() {
    R.phase = "checks";
    rebootBody(h("p", { class: "phase" }, "Back.  Giving the services a minute to start…"));
    rebootButtons(h("button", { type: "button", class: "btn ghost", "data-reboot": "close" }, "Close"));
    await new Promise((res) => setTimeout(res, 45000));
    if (!BB.isLoggedIn()) {
      rebootBody(h("p", { class: "phase" }, "Back.  Log in to run the checks."),
        h("p", { class: "sub" }, "Or run After-reboot checks from the Engineering drawer later."));
      return;
    }
    rebootBody(h("p", { class: "phase" }, "Running the after-reboot checks…"));
    let id = null;
    for (let i = 0; i < 24 && id == null; i++) {    // the runner may still be starting
      const { ok, data } = await BB.post("/api/jobs/start", { job: "post-checks", arg: null });
      if (ok) id = data.id;
      else await new Promise((res) => setTimeout(res, 5000));
    }
    if (id == null) {
      rebootBody(h("p", { class: "phase wait" }, "Couldn't reach the host runner."),
        h("p", { class: "sub" }, "Check it on the server with:  systemctl status bb-runner"));
      return;
    }
    for (let i = 0; i < 60; i++) {
      await new Promise((res) => setTimeout(res, 2000));
      const r = await fetch("/api/jobs", { cache: "no-store" });
      const d = await r.json().catch(() => ({}));
      const run = (d.history || []).find((x) => x.id === id);
      if (run && run.finished) {
        const res = run.result || [];
        rebootBody(h("p", { class: "phase" }, res.every((x) => x.ok) ? "All good." : "Something needs a look."),
          h("div", { class: "checks" }, res.map((x) => BB.row(x.ok ? "ok" : "bad", x.label, x.ok ? "✓" : "✕", x.detail))));
        return;
      }
    }
    rebootBody(h("p", { class: "phase wait" }, "The checks are taking a while.  See History in the drawer."));
  }

  function rebootStop() {
    clearInterval(R.timer);
    clearInterval(R.watch);
    R.timer = R.watch = null;
    R.phase = null;
    $("reboot-dlg").close();
  }

  // --- wiring -------------------------------------------------------------------

  document.addEventListener("click", (ev) => {
    const t = ev.target;
    const pc = t.closest("[data-pc]");
    if (pc) {
      const name = pc.dataset.pc;
      if (name === "reboot") { rebootAsk(); return; }
      if (name === "terminal") { openLink(`terminal/${folderPick}`); showSteps("terminal"); return; }
      openLink(name);
      showSteps(name);
      return;
    }
    if (t.closest("[data-steps-close]")) { BB.showTerm(); return; }
    const pl = t.closest("[data-pclog]");
    if (pl) {
      BB.showLog(`/api/jobs/pclog?file=${encodeURIComponent(pl.dataset.pclog)}`, pl.dataset.pclog);
      return;
    }
    const rb = t.closest("[data-reboot]");
    if (rb) {
      const a = rb.dataset.reboot;
      if (a === "go") rebootCountdown();
      else rebootStop();
    }
  });
  $("reboot-dlg").addEventListener("cancel", (ev) => {
    // Esc cancels the countdown, but doesn't hide a reboot that's under way.
    if (R.phase === "down" || R.phase === "checks") ev.preventDefault();
    else rebootStop();
  });
})();
