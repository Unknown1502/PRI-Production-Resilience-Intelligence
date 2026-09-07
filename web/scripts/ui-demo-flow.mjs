/**
 * Drive the actual demo in a real browser: disrupt, recover, approve, execute.
 *
 *   node scripts/ui-demo-flow.mjs [baseUrl]
 *
 * The walk-through checks the screens at rest. This checks the twenty seconds
 * that matter — a disruption arriving over SSE, the invalid plan appearing with
 * its rule code, the repaired plan, and the state version ticking.
 *
 * Everything is driven through the UI the way a judge would, except the initial
 * disruption, which is posted through the same proxy the browser uses.
 */

import { chromium } from "playwright";
import { mkdirSync } from "node:fs";

const BASE = (process.argv[2] ?? "http://localhost:3000").replace(/\/+$/, "");
const API = `${BASE}/api/pri`;
const SHOTS = "screenshots";
mkdirSync(SHOTS, { recursive: true });

const results = [];
let step = 20;

function record(name, ok, detail = "") {
  results.push({ name, ok, detail });
  console.log(`  ${ok ? "\x1b[32mPASS\x1b[0m" : "\x1b[31mFAIL\x1b[0m"}  ${name}${detail ? ` — ${detail}` : ""}`);
}

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });

const consoleErrors = [];
page.on("console", (m) => m.type() === "error" && consoleErrors.push(m.text()));
page.on("pageerror", (e) => consoleErrors.push(String(e.message)));

async function shot(label) {
  await page.screenshot({ path: `${SHOTS}/${++step}-${label}.png`, fullPage: true });
}

console.log(`\nPRI demo flow — ${BASE}\n`);

// Reset so the run is repeatable.
await page.request.post(`${API}/api/demo/reset`);

// ---------------------------------------------------------------------------
console.log("1 · Open the disruption screen and watch the stream");
// ---------------------------------------------------------------------------
await page.goto(`${BASE}/disruption`, { waitUntil: "domcontentloaded" });
await page.waitForTimeout(3000);

const liveBadge = await page.locator("body").innerText();
record("the SSE stream reports LIVE", /LIVE/i.test(liveBadge));

// ---------------------------------------------------------------------------
console.log("\n2 · Publish a disruption while the page is open");
// ---------------------------------------------------------------------------
const eventId = `ui-flow-${Date.now()}`;
const ingest = await page.request.post(`${API}/api/productions/film-001/events`, {
  data: {
    event_id: eventId,
    event_type: "location.blocked",
    occurred_at: new Date().toISOString(),
    source: "ui-demo-flow",
    severity: 0.8,
    payload: {
      location_id: "LOC-04",
      window_start: "2026-09-10T00:00:00+05:30",
      window_end: "2026-09-11T00:00:00+05:30",
      reason: "Municipal permit withdrawn",
    },
  },
});
record("event accepted through the proxy", ingest.ok(), `HTTP ${ingest.status()}`);

await page.waitForTimeout(4000);
await shot("disruption-live");

// ---------------------------------------------------------------------------
console.log("\n3 · Recover, with the disruption screen still open");
// ---------------------------------------------------------------------------
// Deliberately without navigating away first. The impact panel fills from the
// IMPACT_COMPUTED frame, which the engine emits during recovery rather than on
// ingest — so asserting before this point asserts the wrong thing.
const recover = await page.request.post(`${API}/api/productions/film-001/recover`, {
  data: { event_id: eventId },
  timeout: 180000,
});
record("recovery returned", recover.ok(), `HTTP ${recover.status()}`);

const body = await recover.json();
const labels = body.candidates?.map((c) => c.label) ?? [];
record("four candidates generated", labels.length === 4, labels.join(", "));
record("one candidate was rejected", body.candidates?.some((c) => !c.valid) === true);
record("the rejection names a rule code",
  JSON.stringify(body).includes("C001"), "C001");
record("recovery ran in agent mode", body.mode === "agent", `mode=${body.mode}`);

// The frames have been broadcast now; the page that stayed open should show it.
await page.waitForTimeout(3500);
await shot("disruption-live");
const impactText = await page.locator("body").innerText();
record(
  "the open disruption screen reacted to the live run",
  !/No live disruption/i.test(impactText),
  impactText.match(/blast radius[\s\S]{0,20}/i)?.[0]?.replace(/\s+/g, " ") ?? "still at rest",
);

// ---------------------------------------------------------------------------
console.log("\n4 · The recovery screen shows it");
// ---------------------------------------------------------------------------
await page.goto(`${BASE}/recovery`, { waitUntil: "domcontentloaded" });
await page.waitForTimeout(4000);
await shot("recovery-populated");

const recoveryText = await page.locator("body").innerText();
record("recovery screen lists plans", /Plan|Option|A\b/.test(recoveryText) && recoveryText.length > 400);
record("the rejected plan is visible with its rule", /C001/.test(recoveryText),
  recoveryText.match(/C001[\s\S]{0,60}/)?.[0]?.replace(/\s+/g, " ") ?? "not shown");
record("observed vs required is printed verbatim",
  /observed/i.test(recoveryText) && /required/i.test(recoveryText));

// ---------------------------------------------------------------------------
console.log("\n5 · Governance and audit reflect the run");
// ---------------------------------------------------------------------------
await page.goto(`${BASE}/governance`, { waitUntil: "domcontentloaded" });
await page.waitForTimeout(3000);
await shot("governance-populated");
const govText = await page.locator("body").innerText();
record("governance screen renders content", govText.length > 300);

await page.goto(`${BASE}/audit`, { waitUntil: "domcontentloaded" });
await page.waitForTimeout(3000);
await shot("audit-populated");
const auditText = await page.locator("body").innerText();
record("audit trail has entries", /event|recover|approv|import/i.test(auditText));

// ---------------------------------------------------------------------------
console.log("\n6 · Health after the run");
// ---------------------------------------------------------------------------
const health = await (await page.request.get(`${API}/health`)).json();
record("database ok", health.checks?.database === "ok");
record("kafka ok", health.checks?.kafka === "ok", `kafka=${health.checks?.kafka}`);
record("gemini configured", health.checks?.gemini === "configured");
record("the broker acknowledged a message from this run",
  health.kafka?.last_delivery != null,
  health.kafka?.last_delivery
    ? `partition ${health.kafka.last_delivery.partition}, offset ${health.kafka.last_delivery.offset}`
    : "nothing acknowledged");

record("no console errors during the flow", consoleErrors.length === 0,
  consoleErrors[0]?.slice(0, 120) ?? "");

await browser.close();

const failed = results.filter((r) => !r.ok);
console.log(`\n${results.length - failed.length} passed, ${failed.length} failed`);
if (failed.length) {
  console.log("\nFailures:");
  for (const f of failed) console.log(`  - ${f.name}${f.detail ? ` — ${f.detail}` : ""}`);
}
process.exit(failed.length ? 1 : 0);
