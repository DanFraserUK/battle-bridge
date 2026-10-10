// Screenshots against the dev server (bash tests/dev_restart.sh first).
//   node tests/screens.mjs <outdir> [bridge|jobs|setup|first|narrow]...
// With no names, takes the bridge, graph and login shots.
import { createRequire } from "module";
const require = createRequire(process.env.PLAYWRIGHT_REQUIRE || "/opt/node22/lib/node_modules/");
const { chromium } = require("playwright");

const out = process.argv[2] || ".";
const which = process.argv.slice(3);
const want = (n) => !which.length ? n === "bridge" : which.includes(n);
const base = "http://127.0.0.1:8220";
const browser = await chromium.launch();
const errors = [];
async function page(w = 2560, hgt = 1440) {
  const p = await browser.newPage({ viewport: { width: w, height: hgt } });
  p.on("console", (m) => { if (m.type() === "error") errors.push(m.text()); });
  p.on("pageerror", (e) => errors.push(String(e)));
  return p;
}
async function login(p) {
  await p.click("#login-btn");
  await p.fill("#login-pw", "dev password 1");
  await p.keyboard.press("Enter");
  await p.waitForTimeout(1500);
}

if (want("bridge")) {
  const p = await page();
  await p.goto(base + "/");
  await p.waitForTimeout(5000);
  await p.screenshot({ path: `${out}/bridge.png` });
  await p.click('[data-series="cpu"]');
  await p.waitForTimeout(800);
  await p.mouse.move(1500, 800);
  await p.waitForTimeout(300);
  await p.screenshot({ path: `${out}/graph.png` });
  await p.click("#graph-dlg [data-close]");
  await p.click("#login-btn");
  await p.waitForTimeout(300);
  await p.screenshot({ path: `${out}/login.png` });
  await p.close();
}

if (want("jobs")) {
  const p = await page();
  await p.goto(base + "/");
  await p.waitForTimeout(1500);
  await p.click("#drawer-btn");
  await p.waitForTimeout(800);
  await p.screenshot({ path: `${out}/drawer-loggedout.png` });
  await p.click("#drawer-login");
  await p.fill("#login-pw", "dev password 1");
  await p.keyboard.press("Enter");
  await p.waitForTimeout(2500);
  await p.selectOption("#svc-select", "downloads");
  await p.click('#jobs-pane [data-job="update-one"]');
  await p.waitForTimeout(300);
  await p.screenshot({ path: `${out}/drawer-confirm.png` });
  await p.click("#confirm-yes");
  await p.waitForTimeout(3000);
  await p.screenshot({ path: `${out}/drawer-update.png` });
  await p.selectOption("#svc-select", "myapp");
  await p.click('#jobs-pane [data-job="rebuild"]');
  await p.click("#confirm-yes");
  await p.waitForTimeout(1200);
  await p.click("#term");
  await p.keyboard.type("y");
  await p.keyboard.press("Enter");
  await p.waitForTimeout(2000);
  await p.screenshot({ path: `${out}/drawer-input.png` });
  await p.click('#jobs-pane [data-job="docker-disk-usage"]');
  await p.waitForTimeout(1500);
  await p.screenshot({ path: `${out}/drawer-dropin.png` });
  await p.click('[data-tab="history"]');
  await p.waitForTimeout(600);
  await p.screenshot({ path: `${out}/drawer-history.png` });
  await p.click('[data-pclog="update-system-20261008-091200.log"]');
  await p.waitForTimeout(1000);
  await p.screenshot({ path: `${out}/drawer-pclog.png` });
  await p.click('[data-tab="jobs"]');
  await p.click('[data-pc="terminal"]');
  await p.waitForTimeout(800);
  await p.screenshot({ path: `${out}/drawer-steps.png` });
  await p.click('[data-pc="reboot"]');
  await p.waitForTimeout(300);
  await p.screenshot({ path: `${out}/reboot-ask.png` });
  await p.click('[data-reboot="go"]');
  await p.waitForTimeout(2100);
  await p.screenshot({ path: `${out}/reboot-count.png` });
  await p.click('[data-reboot="cancel"]');
  await p.waitForTimeout(400);
  console.log("reboot dialog open after cancel:", await p.evaluate(() => document.getElementById("reboot-dlg").open));
  await p.close();
}

if (want("setup")) {
  const p = await page(2560, 1440);
  await p.goto(base + "/setup");
  await p.waitForTimeout(800);
  await p.screenshot({ path: `${out}/setup-login.png` });
  await p.fill("#login-pw", "dev password 1");
  await p.keyboard.press("Enter");
  await p.waitForTimeout(1500);
  await p.screenshot({ path: `${out}/setup.png`, fullPage: true });
  await p.close();
}

if (want("first")) {
  const p = await page();
  await p.goto(base + "/");
  await p.waitForTimeout(2500);
  await p.screenshot({ path: `${out}/first-run.png` });
  console.log("first run lands on:", p.url());
  await p.close();
}

if (want("narrow")) {
  const p = await page(1280, 900);
  await p.goto(base + "/");
  await p.waitForTimeout(4000);
  await p.screenshot({ path: `${out}/narrow.png`, fullPage: true });
  await p.close();
}

console.log(errors.length ? "ERRORS:\n" + errors.join("\n") : "no console errors");
await browser.close();
