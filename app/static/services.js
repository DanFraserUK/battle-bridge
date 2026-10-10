// Boxes fed by the other services' APIs (media, downloads, network) and by
// your own panels.d boxes.  Same rule as facts.js: a box is rebuilt only
// when its own data changes.
"use strict";

(function () {
  const BB = window.BB;
  const { h, rebuild, row, problem, pending, svgBar, when } = BB;

  function rate(bytesPerSec) {
    if (bytesPerSec == null) return "–";
    let v = bytesPerSec * 8, i = 0;
    const u = ["b/s", "kb/s", "Mb/s", "Gb/s"];
    while (v >= 1000 && i < u.length - 1) { v /= 1000; i++; }
    return `${v.toFixed(v < 10 && i > 0 ? 1 : 0)} ${u[i]}`;
  }
  function bits(bps) { return bps == null ? "–" : rate(bps / 8); }
  function num(n) { return n == null ? "–" : Number(n).toLocaleString("en-GB"); }
  function dur(sec) {
    if (sec == null || sec < 0) return "–";
    const d = Math.floor(sec / 86400), hr = Math.floor((sec % 86400) / 3600), m = Math.floor((sec % 3600) / 60);
    return d ? `${d} d ${hr} h` : hr ? `${hr} h ${m} m` : `${m} m`;
  }

  // A service's slot: data, or why there isn't any.
  function svc(live, kind) {
    const s = (live.services || {})[kind];
    if (!s) return { wait: true };
    if (!s.found) return { missing: true };
    if (!s.configured) return { notset: true, container: s.container };
    if (s.error) return { error: s.error, container: s.container };
    return { data: s.data, container: s.container };
  }
  function found(live, kind) { return !svc(live, kind).missing; }
  function state(s, label) {
    if (s.wait) return pending(`${label}: waiting for the first answer…`);
    if (s.missing) return null;
    if (s.notset) return h("p", { class: "pending" }, `${label}: found.  Add its key on the `, h("a", { href: "/setup#services" }, "setup page"), ".");
    if (s.error) return problem(`${label}: ${s.error}`);
    return null;
  }
  function head(label, link, extra) {
    return h("div", { class: "shead" },
      link ? h("a", { href: link, target: "_blank", rel: "noopener noreferrer" }, label) : h("b", {}, label),
      extra ? h("span", { class: "sextra" }, extra) : null);
  }
  function linkOf(live, kind) {
    const name = ((live.services || {})[kind] || {}).container;
    const c = (live.containers || []).find((x) => x.name === name);
    return c ? c.link : null;
  }
  function stat(label, value, cls) {
    return h("div", { class: "stat" }, h("span", { class: `sval ${cls || ""}` }, value), h("span", { class: "slbl" }, label));
  }

  // --- media -----------------------------------------------------------------

  function sessions(live, kind, label) {
    const s = svc(live, kind);
    const out = [];
    const list = s.data ? s.data.sessions : [];
    const tc = list.filter((x) => x.transcode).length;
    out.push(head(label, linkOf(live, kind),
      s.data ? (list.length ? `${list.length} watching${tc ? ` · ${tc} transcoding` : ""}` : "nobody watching") : null));
    const st = state(s, label);
    if (st) { out.push(st); return h("div", { class: "scol" }, out); }
    for (const x of list) {
      out.push(h("div", { class: "sess" },
        h("div", { class: "stitle" }, h("span", { class: `pstate ${x.state === "paused" ? "paused" : ""}` }, x.state === "paused" ? "❚❚" : "▶"),
          x.title),
        h("div", { class: "smeta" }, `${x.user} · ${x.device}`,
          x.transcode ? h("span", { class: "badge warn" }, x.hw ? "transcode · GPU" : "transcode · CPU") :
            h("span", { class: "badge" }, "direct")),
        x.progress != null ? svgBar(x.progress, "cyan") : null));
    }
    return h("div", { class: "scol" }, out);
  }

  function media(live) {
    const cols = [["plex", "Plex"], ["jellyfin", "Jellyfin"]].filter(([k]) => found(live, k));
    if (!cols.length) return pending("No Plex or Jellyfin container found.");
    return h("div", { class: cols.length > 1 ? "mcols" : "" }, cols.map(([k, l]) => sessions(live, k, l)));
  }

  // --- downloads -------------------------------------------------------------

  function queue(live, kind, label) {
    const s = svc(live, kind);
    const out = [];
    const d = s.data;
    out.push(head(label, linkOf(live, kind), d ? (d.queue_total ? `${d.queue_total} in queue` : "queue empty") : null));
    const st = state(s, label);
    if (st) return [...out, st];
    for (const q of d.queue.slice(0, 3)) {
      out.push(h("div", { class: "qrow" }, h("span", { class: `qtitle ${q.warn ? "v-warn" : ""}` }, q.title),
        h("span", { class: "qstat" }, q.warn ? "needs a look" : q.progress != null ? `${q.progress}%` : q.status)));
    }
    for (const w of d.health || []) out.push(h("div", { class: `qwarn v-${w.type === "error" ? "bad" : "warn"}` }, w.message));
    return out;
  }

  function downloads(live) {
    const out = [];
    if (found(live, "transmission")) {
      const t = svc(live, "transmission");
      out.push(head("Transmission", linkOf(live, "transmission"), t.data ? `${t.data.active} active of ${t.data.total}` : null));
      const st = state(t, "Transmission");
      if (st) out.push(st);
      else {
        out.push(h("div", { class: "stats" }, stat("down", rate(t.data.down), "v-ok"), stat("up", rate(t.data.up)),
          t.data.errored ? stat("with errors", String(t.data.errored), "v-bad") : null));
        for (const x of t.data.downloading.slice(0, 2)) {
          out.push(h("div", { class: "qrow" }, h("span", { class: "qtitle" }, x.name), h("span", { class: "qstat" }, `${x.progress}%`)));
        }
      }
    }
    const arrs = [["sonarr", "Sonarr"], ["radarr", "Radarr"]].filter(([k]) => found(live, k));
    if (arrs.length) out.push(h("div", { class: "dcols" }, arrs.map(([k, l]) => h("div", {}, queue(live, k, l)))));

    const upcoming = [];
    for (const [n] of arrs) {
      const s = svc(live, n);
      if (s.data) upcoming.push(...s.data.upcoming);
    }
    upcoming.sort((a, b) => (a.when || 0) - (b.when || 0));
    if (upcoming.length) {
      out.push(h("div", { class: "upcoming" }, h("span", { class: "slbl" }, "Next 7 days: "),
        upcoming.slice(0, 4).map((u, i) => [i ? " · " : "", h("b", {}, u.title), ` ${BB.day(u.when)}`])));
    }
    const bottom = [];
    if (found(live, "seerr")) {
      const se = svc(live, "seerr");
      bottom.push(se.data ? row(se.data.pending ? "warn" : "ok", "Requests waiting", String(se.data.pending)) : state(se, "Seerr"));
    }
    if (found(live, "prowlarr")) {
      const pr = svc(live, "prowlarr");
      if (pr.data) {
        const n = pr.data.health.length;
        bottom.push(row(n ? "warn" : "ok", "Prowlarr", n ? `${n} warning${n > 1 ? "s" : ""}` : "healthy",
          n ? pr.data.health[0].message : null));
      } else bottom.push(state(pr, "Prowlarr"));
    }
    if (bottom.length) out.push(h("div", { class: "dcols" }, bottom.map((b) => h("div", {}, b))));
    if (!out.length) return pending("No Transmission, Sonarr, Radarr, Prowlarr or Seerr container found.");
    return out;
  }

  // --- network ---------------------------------------------------------------

  function network(live) {
    const out = [];
    if (found(live, "pihole")) {
      const ph = svc(live, "pihole");
      out.push(head("Pi-hole", linkOf(live, "pihole"), ph.data ? `${num(ph.data.clients)} clients` : null));
      const pst = state(ph, "Pi-hole");
      if (pst) out.push(pst);
      else out.push(h("div", { class: "stats" }, stat("queries today", num(ph.data.total)),
        stat("blocked", `${ph.data.percent != null ? ph.data.percent.toFixed(1) : "–"}%`, "v-ok"),
        stat("on blocklists", num(ph.data.domains))));
    }

    const u = svc(live, "unifi");
    if (!u.missing) {
      out.push(head("UniFi", live.unifi_url, u.data ? `${u.data.gateway || "gateway"} · up ${dur(u.data.uptime)}` : null));
      const ust = state(u, "UniFi");
      if (ust) out.push(ust);
      else out.push(h("div", { class: "stats" }, stat("clients", num(u.data.clients)),
        stat("WAN down", bits(u.data.wan_rx), "v-ok"), stat("WAN up", bits(u.data.wan_tx)),
        u.data.latency != null ? stat("latency", `${u.data.latency} ms`, u.data.latency > 50 ? "v-warn" : "") : null,
        u.data.offline ? stat("devices offline", String(u.data.offline), "v-bad") : null));
    }

    const f = live.facts || {};
    const ip = (f.extip || {}).ip;
    const certs = BB.certSection(f, live.now);
    if (found(live, "ddns") || ip || certs.length) {
      const outside = head("Outside access", null, null);
      if (ip) {
        // Hidden until clicked, and hidden again on the next click or page load.
        const b = h("button", { type: "button", class: "reveal", title: "Click to show or hide" },
          BB.showIp ? `external ${ip}` : "external IP ••••••");
        b.addEventListener("click", () => {
          BB.showIp = !BB.showIp;
          b.textContent = BB.showIp ? `external ${ip}` : "external IP ••••••";
        });
        outside.append(b);
      }
      out.push(outside);
      if (found(live, "ddns")) {
        const dd = svc(live, "ddns");
        if (dd.data) {
          const bad = dd.data.problems.length;
          out.push(row(bad ? "bad" : "ok", "DDNS records", bad ? "problems reported" : `${dd.data.up_to_date.length} up to date`,
            bad ? dd.data.problems[dd.data.problems.length - 1] : null));
        } else out.push(state(dd, "DDNS"));
      }
      if (u.data && u.data.wan_ip && ip && u.data.wan_ip !== ip) {
        out.push(row("warn", "Router and outside disagree", "the two external addresses differ",
          "Checked at different times, or a CGNAT address.  Click the address above to see it."));
      }
      if (certs.length) {
        out.push(certs[0]);
        const det = h("details", { class: "certdet", open: BB.certOpen || null },
          h("summary", {}, "Every certificate"), certs.slice(1));
        det.addEventListener("toggle", () => { BB.certOpen = det.open; });
        out.push(det);
      }
    }
    if (!out.length) {
      return h("p", { class: "pending" }, "No Pi-hole, UniFi gateway, Cloudflare DDNS or Nginx Proxy Manager found.  UniFi is set up on the ",
        h("a", { href: "/setup#services" }, "setup page"), ".");
    }
    return out;
  }

  // --- your own boxes (panels.d) -------------------------------------------------

  function fmtValue(v, unit) {
    if (v == null) return "–";
    if (typeof v === "number") return `${Number.isInteger(v) ? num(v) : v.toLocaleString("en-GB", { maximumFractionDigits: 2 })}${unit ? ` ${unit}` : ""}`;
    return `${v}${unit ? ` ${unit}` : ""}`;
  }

  function panels(live) {
    const p = ((live.facts || {}).panels) || {};
    const out = [];
    for (const item of p.items || []) {
      out.push(head(item.title, item.link, item.read ? `read ${BB.ago(item.read, live.now)}` : null));
      if (item.error) out.push(problem(item.error));
      if (item.values) {
        out.push(h("div", { class: "stats" }, item.values.map((v) => stat(v.label, fmtValue(v.value, v.unit)))));
      }
    }
    for (const msg of p.problems || []) out.push(problem(msg));
    if (!out.length) {
      out.push(pending(live.facts
        ? "Add boxes of your own from any JSON file or address: one small file each in /etc/battle-bridge/panels.d.  See the examples folder."
        : "Your own boxes come from the host runner.  See Setup."));
    }
    return out;
  }

  BB.renderApis = function (live) {
    const sv = live.services || {};
    const strip = (o) => JSON.stringify(o, (k, v) => (k === "checked" ? undefined : v));
    const links = (live.containers || []).map((c) => c.link).join();
    rebuild("media", [strip(sv.plex), strip(sv.jellyfin), links], () => media(live));
    rebuild("downloads", [strip(sv.transmission), strip(sv.sonarr), strip(sv.radarr), strip(sv.seerr), strip(sv.prowlarr), links],
      () => downloads(live));
    const f = live.facts || {};
    rebuild("network", [strip(sv.pihole), strip(sv.unifi), strip(sv.ddns), f.extip, (f.root || {}).certs,
      Math.floor(live.now / 3600), links, live.unifi_url], () => network(live));
    rebuild("panels", [f.panels, !!live.facts, Math.floor(live.now / 60)], () => panels(live));
  };
  BB.when = when;
})();
