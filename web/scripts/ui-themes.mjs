/**
 * Screenshot both themes and check the light one is actually readable.
 *
 *   node scripts/ui-themes.mjs [baseUrl]
 *
 * A light theme built by darkening a dark one usually fails in the same two
 * places: muted text drops below contrast on paper, and a signal colour tuned
 * for a black background disappears on white. Both are measured here rather
 * than eyeballed, against WCAG AA — 4.5:1 for body text, 3:1 for large text.
 */

import { chromium } from "playwright";
import { mkdirSync } from "node:fs";

const BASE = (process.argv[2] ?? "http://localhost:3000").replace(/\/+$/, "");
const SHOTS = "screenshots";
mkdirSync(SHOTS, { recursive: true });

const results = [];
function record(name, ok, detail = "") {
  results.push({ name, ok });
  console.log(`  ${ok ? "\x1b[32mPASS\x1b[0m" : "\x1b[31mFAIL\x1b[0m"}  ${name}${detail ? ` — ${detail}` : ""}`);
}

/** Relative luminance, per WCAG. */
function luminance([r, g, b]) {
  const channel = (v) => {
    const s = v / 255;
    return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
  };
  return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b);
}

function contrast(a, b) {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });

for (const theme of ["dark", "light"]) {
  console.log(`\n${theme === "dark" ? "Strip board (dark)" : "Call sheet (light)"}`);

  await page.goto(`${BASE}/`, { waitUntil: "domcontentloaded" });
  await page.evaluate((t) => {
    localStorage.setItem("pri-theme", t);
  }, theme);
  await page.reload({ waitUntil: "domcontentloaded" });
  await page.waitForTimeout(2500);

  const applied = await page.getAttribute("html", "data-theme");
  record(`data-theme is ${theme}`, applied === theme, `got ${applied}`);

  await page.screenshot({ path: `${SHOTS}/theme-${theme}-overview.png`, fullPage: false });

  // Read the colours the browser actually computed, not the ones we intended.
  const sampled = await page.evaluate(() => {
    const parse = (s) => (s.match(/\d+/g) ?? []).slice(0, 3).map(Number);
    const bg = parse(getComputedStyle(document.body).backgroundColor);
    const pick = (selector) => {
      const el = document.querySelector(selector);
      return el ? parse(getComputedStyle(el).color) : null;
    };
    return {
      bg,
      body: parse(getComputedStyle(document.body).color),
      heading: pick("h1"),
      // Probes, because the tokens are not reliably on a real element on every
      // screen — and a fallback selector silently measured the same colour
      // twice, which made one failure look like two.
      muted: (() => {
        const probe = document.createElement("span");
        probe.className = "text-chalk-400";
        document.body.appendChild(probe);
        const c = parse(getComputedStyle(probe).color);
        probe.remove();
        return c;
      })(),
      faint: (() => {
        const probe = document.createElement("span");
        probe.className = "text-chalk-600";
        document.body.appendChild(probe);
        const c = parse(getComputedStyle(probe).color);
        probe.remove();
        return c;
      })(),
      stamp: (() => {
        const probe = document.createElement("span");
        probe.className = "text-stamp";
        document.body.appendChild(probe);
        const c = parse(getComputedStyle(probe).color);
        probe.remove();
        return c;
      })(),
    };
  });

  const checks = [
    ["body text", sampled.body, 4.5],
    ["heading", sampled.heading, 3],
    ["supporting text", sampled.muted, 4.5],
    ["faintest text", sampled.faint, 4.5],
    ["the refusal red", sampled.stamp, 4.5],
  ];
  for (const [what, colour, need] of checks) {
    if (!colour) continue;
    const ratio = contrast(colour, sampled.bg);
    record(`${what} contrast >= ${need}:1`, ratio >= need, `${ratio.toFixed(2)}:1`);
  }

  // A strip must stay readable in both themes: dark ink on a light card.
  const strip = await page.evaluate(() => {
    const parse = (s) => (s.match(/\d+/g) ?? []).slice(0, 3).map(Number);
    const el = document.querySelector(".strip");
    if (!el) return null;
    return { bg: parse(getComputedStyle(el).backgroundColor), fg: parse(getComputedStyle(el).color) };
  });
  if (strip) {
    const ratio = contrast(strip.fg, strip.bg);
    record("ink on a strip >= 4.5:1", ratio >= 4.5, `${ratio.toFixed(2)}:1`);
  }
}

// The strips must not restyle themselves between themes — the colour is data.
console.log("\nStrip colours are theme-independent");
const stripColours = {};
for (const theme of ["dark", "light"]) {
  await page.evaluate((t) => {
    localStorage.setItem("pri-theme", t);
  }, theme);
  await page.reload({ waitUntil: "domcontentloaded" });
  await page.waitForTimeout(2000);
  stripColours[theme] = await page.evaluate(() =>
    [...document.querySelectorAll(".strip")]
      .slice(0, 6)
      .map((el) => getComputedStyle(el).backgroundColor),
  );
}
record(
  "the four strip colours are identical in both themes",
  JSON.stringify(stripColours.dark) === JSON.stringify(stripColours.light),
  stripColours.dark.slice(0, 2).join(" / "),
);

await browser.close();

const failed = results.filter((r) => !r.ok);
console.log(`\n${results.length - failed.length} passed, ${failed.length} failed`);
process.exit(failed.length ? 1 : 0);
