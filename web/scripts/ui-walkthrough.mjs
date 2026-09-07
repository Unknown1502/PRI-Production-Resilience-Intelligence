/**
 * Drive the PRI console in a real browser and report what actually happens.
 *
 *   node scripts/ui-walkthrough.mjs [baseUrl]
 *
 * Written because "the page returned 200" says nothing about whether it
 * rendered. A Next.js error boundary, a failed client-side fetch, a hydration
 * mismatch and an empty state all return 200 to curl. This clicks through the
 * screens a judge will click through, and fails on anything a judge would see.
 *
 * Three things are watched throughout and reported at the end:
 *   - console errors and page exceptions
 *   - requests that failed or returned >= 400
 *   - any request leaving for the backend host, or carrying an API key
 *
 * The last one is the security assertion: the browser must never talk to the
 * API directly, and must never hold a credential.
 */

import { chromium } from "playwright";
import { mkdirSync } from "node:fs";

const BASE = (process.argv[2] ?? "http://localhost:3000").replace(/\/+$/, "");
const SHOTS = "screenshots";
mkdirSync(SHOTS, { recursive: true });

const consoleErrors = [];
const pageErrors = [];
const badResponses = [];
const leakedRequests = [];
const results = [];

let step = 0;

function record(name, ok, detail = "") {
  results.push({ name, ok, detail });
  const mark = ok ? "\x1b[32mPASS\x1b[0m" : "\x1b[31mFAIL\x1b[0m";
  console.log(`  ${mark}  ${name}${detail ? ` — ${detail}` : ""}`);
}

async function shot(page, label) {
  const file = `${SHOTS}/${String(++step).padStart(2, "0")}-${label}.png`;
  await page.screenshot({ path: file, fullPage: true });
  return file;
}

/** Wait for the page to have actually painted content, not just responded. */
async function settle(page, ms = 2500) {
  await page.waitForLoadState("domcontentloaded");
  await page.waitForTimeout(ms);
}

const browser = await chromium.launch();
const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
const page = await context.newPage();

page.on("console", (msg) => {
  if (msg.type() === "error") consoleErrors.push(`${page.url()} :: ${msg.text()}`);
});
page.on("pageerror", (err) => pageErrors.push(`${page.url()} :: ${err.message}`));

page.on("requestfailed", (req) => {
  // Aborted navigations during fast clicking are noise, not failures.
  const why = req.failure()?.errorText ?? "";
  if (!why.includes("ERR_ABORTED")) badResponses.push(`${req.method()} ${req.url()} — ${why}`);
});

page.on("response", (res) => {
  if (res.status() >= 400) badResponses.push(`${res.status()} ${res.request().method()} ${res.url()}`);
});

page.on("request", (req) => {
  const url = req.url();
  // The browser must never reach the API host directly.
  if (/localhost:8000|:8080|run\.app/.test(url) && !url.startsWith(BASE)) {
    leakedRequests.push(`backend host: ${url}`);
  }
  const headers = req.headers();
  if (headers["x-api-key"]) leakedRequests.push(`API key header: ${req.method()} ${url}`);
});

console.log(`\nPRI console walk-through — ${BASE}\n`);

// ---------------------------------------------------------------------------
console.log("1 · Overview");
// ---------------------------------------------------------------------------
await page.goto(`${BASE}/`, { waitUntil: "domcontentloaded" });
await settle(page);
await shot(page, "overview");

const bodyText = await page.locator("body").innerText();
record("overview renders a production title", /Night Train|Harbour Lights/i.test(bodyText),
  bodyText.slice(0, 60).replace(/\s+/g, " "));
record("overview shows shooting days", /shooting days/i.test(bodyText));
record("no 'Loading' stuck on screen", !/Reading the board/i.test(bodyText));
record("no error banner", !/not answering|Internal Server Error/i.test(bodyText));

const navLinks = await page.locator("nav a").allInnerTexts();
record("left nav has Import a production", navLinks.some((t) => /Import a production/i.test(t)),
  navLinks.join(", "));

// ---------------------------------------------------------------------------
console.log("\n2 · Import screen");
// ---------------------------------------------------------------------------
await page.goto(`${BASE}/import`, { waitUntil: "domcontentloaded" });
await settle(page);
await shot(page, "import");

const importText = await page.locator("body").innerText();
record("drop zone is present", /Drop your production board/i.test(importText));
record("template download offered", /Download the template/i.test(importText));
record("samples listed", /Harbour Lights|Night Train/i.test(importText));
record("seven-sheet contract rendered", /production/i.test(importText) && /availability/i.test(importText));
record("spec loaded from the API (not the fallback)", !/Loading the sheet contract/i.test(importText));

// Upload a real workbook through the proxy.
const sample = "../data/samples/second_unit.xlsx";
await page.locator('input[type="file"]').setInputFiles(sample);
await page.waitForURL(/\/import\/imp/, { timeout: 60000 }).catch(() => {});
await settle(page, 3000);
await shot(page, "import-review");

const reviewUrl = page.url();
record("upload navigated to a review page", /\/import\/imp/.test(reviewUrl), reviewUrl.replace(BASE, ""));

const reviewText = await page.locator("body").innerText();
record("review shows the file summary", /scenes/i.test(reviewText) && /shoot days/i.test(reviewText));
record("review offers Import as version 1", /Import as version 1/i.test(reviewText));
record("schedule health tab present", /Schedule health/i.test(reviewText));

// The planted violation must be visible, since that is the whole point.
const healthTab = page.getByRole("tab", { name: /Schedule health/i });
if (await healthTab.count()) {
  await healthTab.click();
  await page.waitForTimeout(1200);
  await shot(page, "import-schedule-health");
  const healthText = await page.locator("body").innerText();
  record("the planted C001 is shown", /C001/.test(healthText));
}

// ---------------------------------------------------------------------------
console.log("\n3 · Disruption → Recovery");
// ---------------------------------------------------------------------------
await page.goto(`${BASE}/disruption`, { waitUntil: "domcontentloaded" });
await settle(page, 3500);
await shot(page, "disruption");

const disruptionText = await page.locator("body").innerText();
record("disruption screen renders", disruptionText.length > 200);
record("dependency graph present", /graph|scene|impact/i.test(disruptionText));

await page.goto(`${BASE}/recovery`, { waitUntil: "domcontentloaded" });
await settle(page, 3500);
await shot(page, "recovery");
const recoveryText = await page.locator("body").innerText();
record("recovery screen renders", recoveryText.length > 200);

// ---------------------------------------------------------------------------
console.log("\n4 · The remaining screens");
// ---------------------------------------------------------------------------
for (const path of ["/governance", "/verification", "/audit"]) {
  await page.goto(`${BASE}${path}`, { waitUntil: "domcontentloaded" });
  await settle(page, 2500);
  await shot(page, path.slice(1));
  const text = await page.locator("body").innerText();
  record(`${path} renders without an error boundary`,
    text.length > 100 && !/Application error|Unhandled Runtime Error/i.test(text));
}

// ---------------------------------------------------------------------------
console.log("\n5 · Security assertions");
// ---------------------------------------------------------------------------
record("no request reached the backend host directly",
  leakedRequests.filter((l) => l.startsWith("backend host")).length === 0,
  leakedRequests.filter((l) => l.startsWith("backend host"))[0] ?? "");
record("no request carried an API key",
  leakedRequests.filter((l) => l.startsWith("API key")).length === 0,
  leakedRequests.filter((l) => l.startsWith("API key"))[0] ?? "");

const bundleHasKey = await page.evaluate(() =>
  performance.getEntriesByType("resource").map((r) => r.name).join(" "),
);
record("no backend hostname in loaded resources", !/localhost:8000/.test(bundleHasKey));

// ---------------------------------------------------------------------------
console.log("\n6 · Console and network health");
// ---------------------------------------------------------------------------
record("no uncaught page exceptions", pageErrors.length === 0, pageErrors[0] ?? "");
record("no console errors", consoleErrors.length === 0,
  consoleErrors.length ? `${consoleErrors.length}: ${consoleErrors[0].slice(0, 120)}` : "");
record("no failed or 4xx/5xx requests", badResponses.length === 0,
  badResponses.length ? `${badResponses.length}: ${badResponses[0].slice(0, 140)}` : "");

await browser.close();

// ---------------------------------------------------------------------------
const failed = results.filter((r) => !r.ok);
console.log(`\n${results.length - failed.length} passed, ${failed.length} failed`);
if (failed.length) {
  console.log("\nFailures:");
  for (const f of failed) console.log(`  - ${f.name}${f.detail ? ` — ${f.detail}` : ""}`);
}
if (consoleErrors.length) {
  console.log("\nConsole errors:");
  for (const e of consoleErrors.slice(0, 8)) console.log(`  ${e.slice(0, 200)}`);
}
if (badResponses.length) {
  console.log("\nBad responses:");
  for (const r of badResponses.slice(0, 12)) console.log(`  ${r.slice(0, 200)}`);
}
console.log(`\nScreenshots in web/${SHOTS}/`);
process.exit(failed.length ? 1 : 0);
