// The setup page: first-run password, access rules, keys, tile links, PC
// jobs and the host runner.  Every save quotes the settings revision it
// started from, so an old tab can't overwrite newer changes.  Keys are
// write-only: the page only ever learns whether one is set.
"use strict";

(function () {
  const $ = (id) => document.getElementById(id);
  const S = { info: null };

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

  // Fill a section, leaving out any blank parts.
  function put(id, ...kids) { $(id).replaceChildren(...kids.flat().filter((k) => k != null && k !== false)); }

  async function post(path, body) {
    const r = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Battle-Bridge": "1" },
      body: JSON.stringify(body || {}),
    });
    let data = {};
    try { data = await r.json(); } catch (e) { /* empty */ }
    return { ok: r.ok, status: r.status, data };
  }

  function flash(text, bad) {
    const f = $("flash");
    f.textContent = text;
    f.className = bad ? "flash bad" : "flash";
    f.hidden = false;
    clearTimeout(flash.t);
    flash.t = setTimeout(() => { f.hidden = true; }, bad ? 12000 : 4000);
  }

  // --- loading -------------------------------------------------------------------

  async function load() {
    const sess = await (await fetch("/api/session", { cache: "no-store" })).json();
    if (sess.has_password && !sess.logged_in) { show("login-box"); return; }
    const r = await fetch("/api/setup", { cache: "no-store" });
    if (r.status === 401) { show("login-box"); return; }
    const info = await r.json();
    S.info = info;
    if (info.first_run) { show("first-run"); return; }
    $("logout-btn").hidden = false;
    $("setup-nav").hidden = false;
    $("hostname").textContent = "Setup";
    renderAll();
    if (location.hash) {
      const el = document.querySelector(location.hash);
      if (el) el.scrollIntoView();
    }
  }

  function show(id) {
    for (const s of document.querySelectorAll(".setup-box")) s.hidden = s.id !== id;
    $("setup-nav").hidden = true;
    const first = document.querySelector(`#${id} input`);
    if (first) first.focus();
  }

  function renderAll() {
    for (const s of document.querySelectorAll(".setup-box")) s.hidden = ["first-run", "login-box"].includes(s.id);
    renderAccess();
    renderServices();
    renderTiles();
    renderPc();
    renderRunner();
    renderPassword();
  }

  async function save(changes, done) {
    const { ok, data } = await post("/api/settings", { rev: S.info.settings.rev, changes });
    if (!ok) { flash(data.error || "Couldn't save that.", true); return false; }
    S.info.settings = data.settings;
    flash("Saved.");
    if (done) done();
    return true;
  }

  function lines(text) { return text.split(/[\n,]+/).map((x) => x.trim()).filter(Boolean); }
  function field(label, input, note) {
    return h("label", { class: "sfield" }, h("span", { class: "slabel" }, label), input, note ? h("span", { class: "snote" }, note) : null);
  }

  // --- access ------------------------------------------------------------------------

  function renderAccess() {
    const st = S.info.settings;
    const nets = h("textarea", { rows: "7", id: "nets" }, st.allowed_nets.join("\n"));
    const hosts = h("textarea", { rows: "2", id: "hosts", placeholder: "none" }, st.allowed_hosts.join("\n"));
    const server = h("input", { type: "text", id: "server", value: st.server_address,
      placeholder: `blank: the address you use to reach this page (${S.info.page_address || "?"})` });
    const iface = h("input", { type: "text", id: "iface", value: st.net_iface, placeholder: "blank: the busiest physical one" });
    const btn = h("button", { type: "button", class: "btn primary" }, "Save access");
    btn.addEventListener("click", () => save({ allowed_nets: lines(nets.value), allowed_hosts: lines(hosts.value),
      server_address: server.value.trim(), net_iface: iface.value.trim() }, renderAccess));
    put("access", h("h2", {}, "Access"),
      h("p", { class: "sub" }, "Battle Bridge answers only clients on these networks, and only when asked for by address.  "
        + "That keeps it off the internet and stops web pages elsewhere from reaching it through your browser."),
      h("div", { class: "sgrid" },
        field("Allowed networks, one per line", nets, "Your home network.  The defaults are the private ranges."),
        field("Allowed hostnames, one per line", hosts, "Only if you reach it by name, such as bridge.home.  Leave empty to use the address."),
        field("Server address for tile links", server, "For containers on a published port, links use this address."),
        field("Network interface for Net in and out", iface, null)),
      h("div", { class: "row" }, btn),
      h("p", { class: "snote" }, "Locked yourself out?  On the server:  docker exec battle-bridge python3 -m battle_bridge reset-access"));
  }

  // --- services and keys ------------------------------------------------------------

  const KIND_LABELS = { plex: "Plex", jellyfin: "Jellyfin", sonarr: "Sonarr", radarr: "Radarr", prowlarr: "Prowlarr",
    transmission: "Transmission", seerr: "Seerr (or Overseerr, Jellyseerr)", pihole: "Pi-hole v6" };
  const KEY_HELP = {
    plex: "Plex token: sign in to Plex in a browser, open any item's ⋯ menu, Get Info, View XML.  The token is the X-Plex-Token in the address.",
    jellyfin: "Dashboard, API Keys, add one.",
    sonarr: "Settings, General, API Key.",
    radarr: "Settings, General, API Key.",
    prowlarr: "Settings, General, API Key.",
    transmission: "Only if you set a login for its web page.  Blank if it has none.",
    seerr: "Settings, General, API Key.",
    pihole: "Settings, Web interface / API, app password.  Not your login password.",
  };

  function status(kind) {
    const s = (S.info.statuses || {})[kind];
    if (!s) return h("span", { class: "snote" }, "not checked yet");
    if (!s.found) return h("span", { class: "snote" }, "not found");
    if (!s.configured) return h("span", { class: "v-warn" }, s.error);
    if (s.error) return h("span", { class: "v-bad" }, s.error);
    return h("span", { class: "v-ok" }, "working");
  }

  function keyInput(name) {
    const set = S.info.secrets[name];
    const label = S.info.secret_labels[name][1];
    return h("input", { type: "password", "data-secret": name, autocomplete: "off",
      placeholder: set ? `${label}: set (type to replace)` : `${label}: not set`, "aria-label": label });
  }

  function renderServices() {
    const st = S.info.settings;
    const rows = [];
    for (const [kind, label] of Object.entries(KIND_LABELS)) {
      const f = S.info.services[kind] || {};
      const names = Object.entries(S.info.secret_labels).filter(([, v]) => v[0] === kind).map(([k]) => k);
      const addr = h("input", { type: "text", "data-addr": kind, value: f.override || "",
        placeholder: f.address || "no address found: enter one", "aria-label": `${label} address` });
      const clear = names.filter((n) => S.info.secrets[n]).length
        ? h("button", { type: "button", class: "btn ghost small", "data-clear": names.join(",") }, "Clear key") : null;
      rows.push(h("tr", { class: f.container ? null : "dim" },
        h("td", {}, h("b", {}, label), h("div", { class: "snote" }, f.container ? `container ${f.container}` : "no container found")),
        h("td", {}, addr),
        h("td", {}, names.map(keyInput), clear, h("div", { class: "snote" }, KEY_HELP[kind])),
        h("td", {}, status(kind))));
    }
    const saveBtn = h("button", { type: "button", class: "btn primary" }, "Save services and keys");
    saveBtn.addEventListener("click", saveServices);

    const u = st.unifi;
    const uUrl = h("input", { type: "text", id: "unifi-url", value: u.url, placeholder: "https://192.168.1.1" });
    const uKey = keyInput("unifi_key");
    const pin = h("button", { type: "button", class: "btn ghost small", id: "unifi-pin" }, u.fingerprint ? "Pin again" : "Pin its certificate");
    pin.addEventListener("click", pinUnifi);
    put("services", h("h2", {}, "Services and keys"),
      h("p", { class: "sub" }, "Found by their image.  Keys stay on the server: this page never shows them again, and they're never sent to the browser.  "
        + "Leave an address blank to use the one found."),
      h("table", { class: "stable" },
        h("thead", {}, h("tr", {}, h("th", {}, "Service"), h("th", {}, "Address Battle Bridge uses"), h("th", {}, "Key"), h("th", {}, "Status"))),
        h("tbody", {}, rows)),
      h("h3", { class: "minor" }, "UniFi gateway (UniFi OS with the Integrations API)"),
      h("div", { class: "sgrid" },
        field("Gateway address", uUrl, "Usually https:// and your router's address."),
        field("API key", uKey, "UniFi Network, Settings, Control Plane, Integrations, Create New API Key."),
        h("div", { class: "sfield" }, h("span", { class: "slabel" }, "Certificate"),
          h("span", { class: "snote" }, u.fingerprint ? `pinned: ${u.fingerprint.slice(0, 16)}…` : "not pinned yet.  The key is only sent once it's pinned."),
          pin, status("unifi"))),
      h("div", { class: "row" }, saveBtn));
  }

  async function saveServices() {
    const services = {};
    document.querySelectorAll("[data-addr]").forEach((i) => { if (i.value.trim()) services[i.dataset.addr] = { url: i.value.trim() }; });
    const unifiUrl = $("unifi-url").value.trim();
    const st = S.info.settings;
    const ok = await save({ services, unifi: { url: unifiUrl, fingerprint: unifiUrl === st.unifi.url ? st.unifi.fingerprint : "" } });
    if (!ok) return;
    const secrets = {};
    document.querySelectorAll("[data-secret]").forEach((i) => { if (i.value) secrets[i.dataset.secret] = i.value; });
    if (Object.keys(secrets).length) {
      const { ok: ok2, data } = await post("/api/secrets", secrets);
      if (!ok2) { flash(data.error || "Couldn't save the keys.", true); return; }
      S.info.secrets = data.secrets;
    }
    // A new gateway address with a key: pin its certificate straight away.
    if (unifiUrl.startsWith("https://") && !S.info.settings.unifi.fingerprint && S.info.secrets.unifi_key) await pinUnifi();
    flash("Saved.  Statuses update within a minute.");
    setTimeout(load, 2500);
    renderServices();
  }

  async function pinUnifi() {
    const { ok, data } = await post("/api/unifi/pin", { rev: S.info.settings.rev });
    if (!ok) { flash(data.error || "Couldn't pin it.", true); return; }
    S.info.settings = data.settings;
    flash("Certificate pinned.");
    renderServices();
  }

  // --- tiles and links ----------------------------------------------------------------

  function renderTiles() {
    const rows = (S.info.containers || []).map((c) => {
      const input = h("input", { type: "text", "data-link": c.name, value: c.override || "",
        placeholder: "blank: automatic.  none: no link", "aria-label": `Link for ${c.name}` });
      return h("tr", {},
        h("td", {}, h("b", {}, c.label), c.label !== c.name ? h("div", { class: "snote" }, c.name) : null),
        h("td", { class: "mono" }, c.image),
        h("td", {}, c.link ? h("a", { href: c.link, target: "_blank", rel: "noopener noreferrer" }, c.link) : h("span", { class: "snote" }, "no link"),
          h("div", { class: "snote" }, c.source === "none" ? "" : `from: ${c.source}`)),
        h("td", {}, input));
    });
    const btn = h("button", { type: "button", class: "btn primary" }, "Save links");
    btn.addEventListener("click", () => {
      const links = {};
      document.querySelectorAll("[data-link]").forEach((i) => { if (i.value.trim()) links[i.dataset.link] = i.value.trim(); });
      save({ links }, () => setTimeout(load, 300));
    });
    put("tiles", h("h2", {}, "Tiles and links"),
      h("p", { class: "sub" }, "Each container gets a tile.  Its link comes from a link typed here, then Docker labels "
        + "(battlebridge.url, or battlebridge.port and battlebridge.path), then built-in defaults for common images, then a quick probe of its ports.  "
        + "{server} in a link is replaced by the server's address."),
      rows.length ? h("table", { class: "stable" },
        h("thead", {}, h("tr", {}, h("th", {}, "Container"), h("th", {}, "Image"), h("th", {}, "Link now"), h("th", {}, "Your link"))),
        h("tbody", {}, rows)) : h("p", { class: "err" }, "No containers found.  Is the Docker proxy running?"),
      h("div", { class: "row" }, btn));
  }

  // --- PC jobs ------------------------------------------------------------------------

  function renderPc() {
    const p = S.info.settings.pc;
    const term = h("select", { id: "pc-term" }, Object.entries(S.info.terminals).map(([k, v]) =>
      h("option", { value: k, selected: k === p.terminal || null }, v)));
    const target = h("input", { type: "text", id: "pc-target", value: p.ssh_target, placeholder: `you@${S.info.server_address}` });
    const port = h("input", { type: "number", id: "pc-port", value: String(p.ssh_port), min: "1", max: "65535" });
    const found = ["~", ...S.info.folders_found];
    const extra = p.folders.filter((f) => !found.includes(f));
    const boxes = found.map((f) => h("label", { class: "check" },
      h("input", { type: "checkbox", "data-folder": f, checked: p.folders.includes(f) || null }), f === "~" ? "home folder (~)" : f));
    const more = h("textarea", { rows: "2", id: "pc-more", placeholder: "/srv/other-folder" }, extra.join("\n"));
    const btn = h("button", { type: "button", class: "btn primary" }, "Save PC jobs");
    btn.addEventListener("click", () => {
      const folders = [...document.querySelectorAll("[data-folder]")].filter((i) => i.checked).map((i) => i.dataset.folder)
        .concat(lines($("pc-more").value));
      save({ pc: { terminal: term.value, ssh_target: target.value.trim(), ssh_port: Number(port.value) || 22, folders } }, renderPc);
    });
    const ready = !!p.ssh_target;
    put("pc", h("h2", {}, "PC jobs"),
      h("p", { class: "sub" }, "Jobs that need sudo never run in the page.  A button opens a terminal on your Linux desktop, "
        + "SSH'd into the server, and sudo asks for your password there.  A small link handler on your PC makes that work.  "
        + "It only accepts update system, reboot, and a terminal in one of the folders below."),
      h("div", { class: "sgrid" },
        field("Terminal on your PC", term, null),
        field("SSH login for the server", target, "As you'd type after ssh.  Key login is easiest."),
        field("SSH port", port, null)),
      h("div", { class: "sfield" }, h("span", { class: "slabel" }, "Folders a terminal can open in"), h("div", { class: "checks-list" }, boxes),
        field("Other folders, one per line", more, "Change these, and download the handler again.")),
      h("div", { class: "row" }, btn),
      h("h3", { class: "minor" }, "Install the link handler"),
      ready ? h("ol", { class: "steps-list" },
        h("li", {}, "On your PC: ", h("a", { class: "btn small", href: "/api/pc/installer", download: "battlebridge-pc-install.sh" }, "Download the installer")),
        h("li", {}, "Then run it as yourself (not root):", h("code", { class: "cmd" }, "bash ~/Downloads/battlebridge-pc-install.sh")),
        h("li", {}, "The first time you press a PC job button, your browser asks whether to open battlebridge:// links.  Allow it."))
        : h("p", { class: "pending" }, "Save an SSH login first."));
  }

  // --- host runner ---------------------------------------------------------------------

  function renderRunner() {
    const r = S.info.runner;
    const installed = !!r.version;
    const same = r.version === r.app_version;
    put("runner", h("h2", {}, "Host runner"),
      h("p", { class: "sub" }, "Optional.  A small service on the server that runs jobs from a fixed list (restart, update, logs, prune and so on), "
        + "and reads what the container can't: drives and their health, updates waiting, the reboot light, timers, and your own drop-in jobs, checks and boxes."),
      h("p", {}, installed ? (same ? h("span", { class: "v-ok" }, `Installed, version ${r.version}.`)
        : h("span", { class: "v-warn" }, `Installed, version ${r.version}.  This page is ${r.app_version}: run the line below again to update it.`))
        : h("span", { class: "v-warn" }, "Not installed.")),
      r.problem && installed ? h("p", { class: "err" }, r.problem) : null,
      h("p", {}, installed ? "To update it after a new release, run this on the server again:" : "To install it, run this on the server (it asks once before changing anything):"),
      h("code", { class: "cmd" }, S.info.install_command),
      h("p", { class: "snote" }, "Drop-in files go in /etc/battle-bridge/jobs.d, checks.d and panels.d.  See the examples folder in the repo."));
  }

  // --- password ------------------------------------------------------------------------

  function renderPassword() {
    const cur = h("input", { type: "password", id: "pw-cur", autocomplete: "current-password" });
    const nw = h("input", { type: "password", id: "pw-new", autocomplete: "new-password", minlength: "10" });
    const nw2 = h("input", { type: "password", id: "pw-new2", autocomplete: "new-password", minlength: "10" });
    const btn = h("button", { type: "button", class: "btn primary" }, "Change password");
    btn.addEventListener("click", async () => {
      if (nw.value !== nw2.value) { flash("The new passwords don't match.", true); return; }
      const { ok, data } = await post("/api/password", { current: cur.value, new: nw.value });
      cur.value = nw.value = nw2.value = "";
      if (!ok) { flash(data.error || "Couldn't change it.", true); return; }
      flash("Password changed.  Other logins have been logged out.");
    });
    put("password", h("h2", {}, "Password"),
      h("div", { class: "sgrid" }, field("Current password", cur), field("New password", nw, "Ten characters at least."), field("Same again", nw2)),
      h("div", { class: "row" }, btn),
      h("p", { class: "snote" }, "Forgotten it?  On the server:  docker exec -it battle-bridge python3 -m battle_bridge set-password"));
  }

  // --- forms and wiring --------------------------------------------------------------

  $("first-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const err = $("first-err");
    err.hidden = true;
    if ($("first-pw").value !== $("first-pw2").value) {
      err.textContent = "The passwords don't match.";
      err.hidden = false;
      return;
    }
    const { ok, data } = await post("/api/first-run", { code: $("first-code").value, password: $("first-pw").value });
    if (!ok) { err.textContent = data.error || "That didn't work."; err.hidden = false; return; }
    $("first-pw").value = $("first-pw2").value = "";
    flash("Password set.  Now the rest of the setup.  You can come back here any time.");
    load();
  });

  $("login-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const err = $("login-err");
    err.hidden = true;
    const { ok, data } = await post("/api/login", { password: $("login-pw").value });
    $("login-pw").value = "";
    if (!ok) { err.textContent = data.error || "Login failed."; err.hidden = false; return; }
    load();
  });

  $("logout-btn").addEventListener("click", async () => {
    await post("/api/logout");
    location.href = "/";
  });

  document.addEventListener("click", async (ev) => {
    const c = ev.target.closest("[data-clear]");
    if (!c) return;
    const body = {};
    for (const n of c.dataset.clear.split(",")) body[n] = "";
    const { ok, data } = await post("/api/secrets", body);
    if (!ok) { flash(data.error || "Couldn't clear it.", true); return; }
    S.info.secrets = data.secrets;
    flash("Key cleared.");
    renderServices();
  });

  // Enter never submits anything except the two login forms.
  document.addEventListener("keydown", (ev) => {
    if (ev.key === "Enter" && ev.target.matches("input, select") && !ev.target.closest("#first-form, #login-form")) {
      ev.preventDefault();
    }
  });

  load();
})();
