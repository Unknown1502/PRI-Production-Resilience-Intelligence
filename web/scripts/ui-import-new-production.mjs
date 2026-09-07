/**
 * Import a production PRI has never seen, through the deployed browser path.
 *
 *   node scripts/ui-import-new-production.mjs <baseUrl> <path-to-xlsx>
 *
 * Everything else tests the fixture or the bundled sample, both of which the
 * code was written alongside. This drops a third film — different country,
 * currency, timezone, a company move and a hold on the board, one deliberate
 * turnaround breach — onto the live site the way a judge would, and then keeps
 * going: commit it, read it back, render its call sheet, disrupt it, recover
 * it. A production that imports but cannot be operated has not been imported.
 */

import { chromium } from "playwright";
import { mkdirSync } from "node:fs";

const BASE = (process.argv[2] ?? "http://localhost:3000").replace(/\/+$/, "");
const FILE = process.argv[3];
const API = `${BASE}/api/pri`;
const SHOTS = "screenshots";
mkdirSync(SHOTS, { recursive: true });

if (!FILE) {
  console.error("usage: node scripts/ui-import-new-production.mjs <baseUrl> <file.xlsx>");
  process.exit(2);
}

const results = [];
let step = 40;
function record(name, ok, detail = "") {
  results.push({ name, ok });
  console.log(`  ${ok ? "\x1b[32mPASS\x1b[0m" : "\x1b[31mFAIL\x1b[0m"}  ${name}${detail ? ` — ${detail}` : ""}`);
}

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
const consoleErrors = [];
page.on("console", (m) => m.type() === "error" && consoleErrors.push(m.text()));
page.on("pageerror", (e) => consoleErrors.push(String(e.message)));

const shot = async (label) =>
  page.screenshot({ path: `${SHOTS}/${++step}-${label}.png`, fullPage: true });

console.log(`\nImporting a new production — ${BASE}\n`);

// ---------------------------------------------------------------------------
console.log("1 · Drop the file on the import screen");
// ---------------------------------------------------------------------------
await page.goto(`${BASE}/import`, { waitUntil: "domcontentloaded" });
await page.waitForTimeout(2500);
await page.locator('input[type="file"]').setInputFiles(FILE);
await page.waitForURL(/\/import\/imp/, { timeout: 90000 }).catch(() => {});
await page.waitForTimeout(3000);
await shot("new-import-review");

record("upload reached a review page", /\/import\/imp/.test(page.url()), page.url().replace(BASE, ""));

const review = await page.locator("body").innerText();
record("PRI read the workbook without errors", !/\d+ problems? stop PRI/i.test(review));
record("the new title is shown", /Salt and Nightfall/i.test(review) || /salt-and-nightfall/i.test(review));
record("it counted the scenes", /\b9\b/.test(review));

// The planted breach must be reported — and must not block.
const healthTab = page.getByRole("tab", { name: /Schedule health/i });
if (await healthTab.count()) {
  await healthTab.click();
  await page.waitForTimeout(1200);
  await shot("new-import-health");
  const health = await page.locator("body").innerText();
  record("the turnaround breach already on the board is reported", /C001/.test(health),
    health.match(/observed[\s\S]{0,24}/i)?.[0]?.replace(/\s+/g, " ") ?? "");
}
record("an existing violation does not block the import", /Import as version 1/i.test(review));

// ---------------------------------------------------------------------------
console.log("\n2 · Commit it as version 1");
// ---------------------------------------------------------------------------
await page.getByPlaceholder("your name").fill("import smoke test");
await page.getByRole("button", { name: /Import as version 1/i }).click();
await page.waitForURL(/\?production=/, { timeout: 90000 }).catch(() => {});
await page.waitForTimeout(4000);
await shot("new-production-board");

const boardUrl = page.url();
const PRODUCTION = new URL(boardUrl).searchParams.get("production") ?? "";
record("committing opened the new production", /^film-sn-/.test(PRODUCTION), PRODUCTION);

const board = await page.locator("body").innerText();
record("its board renders", /Salt and Nightfall/i.test(board));
record("the strips carry its own scenes", /lighthouse door|cliff path|Bica/i.test(board));
record("the company move is labelled, not shown as an empty shoot day",
  /Company move/i.test(board));
record("the held day is labelled", /On hold/i.test(board));

// ---------------------------------------------------------------------------
console.log("\n3 · Operate it: call sheet, disruption, recovery");
// ---------------------------------------------------------------------------
const schedule = await (await page.request.get(`${API}/api/productions/${PRODUCTION}/schedule`)).json();
const shootDay = schedule.days.find((d) => d.scenes.length > 0);
record("the API serves its schedule", Boolean(shootDay), `${schedule.days.length} days`);

const pdf = await page.request.get(
  `${API}/api/productions/${PRODUCTION}/call-sheet/${shootDay.date}`,
);
const bytes = Buffer.from(await pdf.body());
record("a call sheet renders as a real PDF", bytes.subarray(0, 4).toString() === "%PDF",
  `${bytes.length} bytes`);
// The address comes from the workbook's own locations sheet, which is the
// whole point: logistics.yaml knows nothing about this film.
record("the PDF is substantial, not a stub", bytes.length > 2000);

const eventId = `newprod-${Date.now()}`;
const ingest = await page.request.post(`${API}/api/productions/${PRODUCTION}/events`, {
  data: {
    event_id: eventId,
    event_type: "location.blocked",
    occurred_at: new Date().toISOString(),
    source: "import-smoke-test",
    severity: 0.8,
    // The window matters: impact_of derives the affected days from it, and an
    // event without one blocks nothing, so recovery correctly returns no
    // candidates. Easy to leave out, and it looks like a broken engine.
    payload: {
      location_id: "SN-CLIFF",
      window_start: "2027-05-03T00:00:00+01:00",
      window_end: "2027-05-05T00:00:00+01:00",
      reason: "Cliff path closed by the parish council",
    },
  },
});
record("a disruption against it is accepted", ingest.ok(), `HTTP ${ingest.status()}`);

const recover = await page.request.post(`${API}/api/productions/${PRODUCTION}/recover`, {
  data: { event_id: eventId },
  timeout: 180000,
});
record("recovery runs on it", recover.ok(), `HTTP ${recover.status()}`);
if (recover.ok()) {
  const body = await recover.json();
  record("it produced candidates", (body.candidates?.length ?? 0) > 0,
    (body.candidates ?? []).map((c) => c.label).join(", "));
  // `deterministic` is a pass here. If Gemini narrates without calling
  // generate_and_evaluate, PRI discards the answer and recomputes rather than
  // trusting it — so the mode reports which path produced the numbers, and
  // both are correct outcomes.
  record("recovery ran by a known path", ["agent", "deterministic"].includes(body.mode),
    `mode=${body.mode}`);
}

// ---------------------------------------------------------------------------
console.log("\n4 · Confluent saw it");
// ---------------------------------------------------------------------------
await page.waitForTimeout(6000);
const health = await (await page.request.get(`${API}/health`)).json();
record("kafka reachable", health.checks?.kafka === "ok", `kafka=${health.checks?.kafka}`);
record("the broker acknowledged a message", health.kafka?.last_delivery != null,
  health.kafka?.last_delivery
    ? `${health.kafka.last_delivery.topic} partition ${health.kafka.last_delivery.partition} offset ${health.kafka.last_delivery.offset}`
    : `delivered=${health.kafka?.delivered} failed=${health.kafka?.failed} last_error=${health.kafka?.last_error}`);
record("database ok", health.checks?.database === "ok");
record("gemini configured", health.checks?.gemini === "configured");

record("no console errors throughout", consoleErrors.length === 0, consoleErrors[0]?.slice(0, 120) ?? "");

await browser.close();

const failed = results.filter((r) => !r.ok);
console.log(`\n${results.length - failed.length} passed, ${failed.length} failed`);
if (failed.length) {
  console.log("\nFailures:");
  for (const f of failed) console.log(`  - ${f.name}`);
}
process.exit(failed.length ? 1 : 0);
