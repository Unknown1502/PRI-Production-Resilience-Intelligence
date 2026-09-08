/**
 * The screens a judge sees before pressing anything.
 *
 * Four of seven routes opened blank. Someone who explores first and presses
 * later concluded the system was broken, which is the real reason the demo
 * only ever worked from the button. Every empty screen must now name what
 * belongs there and offer a way to make it happen.
 *
 *   node scripts/ui-empty-states.mjs <baseUrl>
 */

import { chromium } from "playwright";
import { mkdirSync } from "node:fs";

const BASE = (process.argv[2] ?? "http://localhost:3111").replace(/\/+$/, "");
const SHOTS = "screenshots";
mkdirSync(SHOTS, { recursive: true });

const results = [];
function record(name, ok, detail = "") {
  results.push({ name, ok });
  console.log(
    `  ${ok ? "\x1b[32mPASS\x1b[0m" : "\x1b[31mFAIL\x1b[0m"}  ${name}${detail ? ` — ${detail}` : ""}`,
  );
}

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });

for (const route of ["/recovery", "/verification", "/audit"]) {
  console.log(`\n${route}`);
  await page.goto(`${BASE}${route}`, { waitUntil: "domcontentloaded" });
  await page.waitForTimeout(2500);

  const body = await page.locator("body").innerText();

  record(
    `${route}: the screen explains itself`,
    body.length > 400 && !/^\s*$/.test(body),
    `${body.length} chars of copy`,
  );
  record(
    `${route}: offers a way to start the pipeline`,
    await page
      .getByRole("button", { name: /Report a disruption/i })
      .first()
      .isVisible()
      .catch(() => false),
  );

  await page.screenshot({ path: `${SHOTS}/empty${route.replace("/", "-")}.png`, fullPage: true });
}

// Verification shows the contract before it runs.
await page.goto(`${BASE}/verification`, { waitUntil: "domcontentloaded" });
await page.waitForTimeout(2000);
const verification = await page.locator("body").innerText();
for (const code of ["V1", "V2", "V3", "V4", "V5", "V6"]) {
  record(`/verification: ${code} is named before it runs`, verification.includes(code));
}
record(
  "/verification: the pending checks are marked not run",
  /Not run/i.test(verification),
  "dimmed and labelled, not mistakable for a result",
);

// Audit shows the ledger's shape without inventing an entry.
await page.goto(`${BASE}/audit`, { waitUntil: "domcontentloaded" });
await page.waitForTimeout(2000);
const audit = await page.locator("body").innerText();
record("/audit: the columns are visible", /time/.test(audit) && /actor/.test(audit) && /action/.test(audit));
record("/audit: the example row is labelled as one", /example/i.test(audit));
record(
  "/audit: it says nothing was recorded",
  /No entry above was recorded/i.test(audit),
  "a placeholder that reads as real would be worse than a blank panel",
);

await browser.close();

const failed = results.filter((r) => !r.ok);
console.log(`\n${results.length - failed.length} passed, ${failed.length} failed`);
if (failed.length) {
  console.log("\nFailures:");
  for (const f of failed) console.log(`  - ${f.name}`);
}
process.exit(failed.length ? 1 : 0);
