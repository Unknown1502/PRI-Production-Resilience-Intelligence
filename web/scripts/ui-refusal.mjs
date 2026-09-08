/**
 * The refusal, which is the console's best argument, treated as a screen.
 *
 * An agent that declines input it cannot ground is the cheapest possible proof
 * that the plan was not invented. It used to render as a small grey box that
 * read like a form validation error.
 *
 *   node scripts/ui-refusal.mjs <baseUrl> <passcode>
 */

import { chromium } from "playwright";
import { mkdirSync } from "node:fs";

const BASE = (process.argv[2] ?? "http://localhost:3111").replace(/\/+$/, "");
const PASSCODE = process.argv[3] ?? "";
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
const page = await browser.newPage({ viewport: { width: 1440, height: 950 } });
await page.goto(`${BASE}/disruption`, { waitUntil: "domcontentloaded" });
await page.waitForTimeout(3000);

const body = await page.locator("body").innerText();

// 1 — the closed set is on screen before anything is typed.
record(
  "the production's locations are visible up front",
  /Mattancherry Courtyard|Harbour Warehouse/i.test(body),
);
record("the production's cast is visible up front", /Arun|Meera/i.test(body));

// 2 — the passcode is folded away.
const passcodeVisible = await page
  .getByLabel("Injection passcode")
  .isVisible()
  .catch(() => false);
record(
  "the passcode is not the first thing a judge meets",
  /Presenter access/i.test(body),
  passcodeVisible ? "fold is open (no saved passcode)" : "fold is closed",
);

// 3 — a report naming something that does not exist is refused, loudly.
// The fold starts open when no passcode is saved and closed when one is, so
// open it only if it is shut — clicking regardless would close it.
if (!(await page.getByLabel("Injection passcode").isVisible().catch(() => false))) {
  await page.getByText("Presenter access").click();
}
await page.getByLabel("Injection passcode").fill(PASSCODE);
await page
  .getByLabel("What happened")
  .fill("The lighthouse at Cabo da Roca is closed by the coastguard on Thursday");
await page.getByRole("button", { name: /Send it through/i }).click();
await page.waitForTimeout(45000);

const refusal = await page.locator("body").innerText();
record(
  "the refusal is a headline, not an error toast",
  /PRI declined this/i.test(refusal),
);
record(
  "it says why in the architecture's own terms",
  /does not invent entities/i.test(refusal),
);

const chips = page.locator("button", { hasText: /Courtyard|Warehouse|Stage|Street|Arun|Meera/ });
const chipCount = await chips.count();
record("it offers real alternatives as chips", chipCount > 0, `${chipCount} chips`);

if (chipCount > 0) {
  const label = (await chips.first().innerText()).trim();
  await chips.first().click();
  await page.waitForTimeout(500);
  const typed = await page.getByLabel("What happened").inputValue();
  record(
    "a chip writes itself into the box",
    typed.includes(label),
    `"${typed.slice(0, 60)}"`,
  );
}

await page.screenshot({ path: `${SHOTS}/refusal.png`, fullPage: true });
await browser.close();

const failed = results.filter((r) => !r.ok);
console.log(`\n${results.length - failed.length} passed, ${failed.length} failed`);
if (failed.length) {
  console.log("\nFailures:");
  for (const f of failed) console.log(`  - ${f.name}`);
}
process.exit(failed.length ? 1 : 0);
