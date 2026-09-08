/**
 * What a first-time visitor can tell about PRI without scrolling.
 *
 * The console opened onto a strip board with no strapline and no instruction:
 * a film schedule, seven nouns in a sidebar, and a Reset button. Nothing said
 * what the system claimed to do, and the claim is the interesting part.
 *
 *   node scripts/ui-orientation.mjs <baseUrl>
 */

import { chromium } from "playwright";
import { mkdirSync } from "node:fs";

const BASE = (process.argv[2] ?? "http://localhost:3112").replace(/\/+$/, "");
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

// --- a cold visitor at the stated width -----------------------------------
const page = await browser.newPage({ viewport: { width: 1280, height: 800 } });
await page.goto(BASE, { waitUntil: "domcontentloaded" });
await page.waitForTimeout(2500);

const body = await page.locator("body").innerText();
record("the header says what the system does", /Refuses plans that break the rules/i.test(body));
record("the band states the claim", /computes what a disruption actually costs/i.test(body));
record("the pipeline is decoded in nav order", /a disruption arrives/i.test(body) && /audit log records/i.test(body));
record(
  "there is exactly one next action",
  (await page.getByRole("link", { name: /See it recover from a disruption/i }).count()) === 1,
);

// It has to be readable without scrolling.
const bandBox = await page
  .locator("section", { hasText: /computes what a disruption actually costs/i })
  .first()
  .boundingBox();
record(
  "it is above the fold",
  bandBox ? bandBox.y + bandBox.height <= 800 : false,
  bandBox ? `band ends at y=${Math.round(bandBox.y + bandBox.height)}` : "not found",
);

// The strapline must not wrap or displace the state chip.
const strap = await page.evaluate(() => {
  const p = [...document.querySelectorAll("header p")].find((n) =>
    /Refuses plans/.test(n.textContent ?? ""),
  );
  if (!p) return null;
  const cs = getComputedStyle(p);
  return {
    lines: Math.round(p.getBoundingClientRect().height / Number.parseFloat(cs.lineHeight || "16")),
    truncates: cs.textOverflow === "ellipsis",
    chars: (p.textContent ?? "").trim().length,
  };
});
record(
  "the strapline is one line and truncates",
  strap ? strap.lines <= 1 && strap.truncates : false,
  strap ? `${strap.chars} chars, ${strap.lines} line, ellipsis:${strap.truncates}` : "not found",
);

// The signal colours stay reserved for refused/verified.
const usesSignal = await page.evaluate(() => {
  const band = [...document.querySelectorAll("section")].find((n) =>
    /computes what a disruption actually costs/i.test(n.textContent ?? ""),
  );
  if (!band) return true;
  return [band, ...band.querySelectorAll("*")].some((el) => {
    const cs = getComputedStyle(el);
    return /stamp|seal/.test(el.className?.toString?.() ?? "") ||
      cs.borderColor.includes("rgb(178") ;
  });
});
record("it takes no signal colour", !usesSignal);

await page.screenshot({ path: `${SHOTS}/orientation.png`, fullPage: false });

// --- dismissal survives a reload, and the ? brings it back ----------------
await page.getByRole("button", { name: /Dismiss this introduction/i }).click();
await page.waitForTimeout(400);
record("dismissing hides it", !/computes what a disruption actually costs/i.test(await page.locator("body").innerText()));

await page.reload({ waitUntil: "domcontentloaded" });
await page.waitForTimeout(2000);
record(
  "the dismissal survives a reload",
  !/computes what a disruption actually costs/i.test(await page.locator("body").innerText()),
);

await page.getByRole("link", { name: /Show the introduction/i }).click();
await page.waitForTimeout(1200);
record(
  "the ? control brings it back",
  /computes what a disruption actually costs/i.test(await page.locator("body").innerText()),
);
await page.close();

// --- localStorage throwing on every access --------------------------------
const hostile = await browser.newPage({ viewport: { width: 1280, height: 800 } });
await hostile.addInitScript(() => {
  const boom = () => {
    throw new Error("site data blocked");
  };
  Object.defineProperty(window, "localStorage", {
    configurable: true,
    get: boom,
  });
});
await hostile.goto(BASE, { waitUntil: "domcontentloaded" });
await hostile.waitForTimeout(3000);
const hostileBody = await hostile.locator("body").innerText();
record(
  "the board still renders with storage blocked",
  /shooting days/i.test(hostileBody),
  "the explainer must never take the page down with it",
);
record("the band still shows with storage blocked", /computes what a disruption actually costs/i.test(hostileBody));
await hostile.close();

await browser.close();

const failed = results.filter((r) => !r.ok);
console.log(`\n${results.length - failed.length} passed, ${failed.length} failed`);
if (failed.length) {
  console.log("\nFailures:");
  for (const f of failed) console.log(`  - ${f.name}`);
}
process.exit(failed.length ? 1 : 0);
