/**
 * The Live Disruption screen, measured rather than eyeballed.
 *
 * Both defects this checks for were invisible to every existing suite: the
 * graph rendered *outside* its row and on top of the Event feed, which is a
 * geometry fault no assertion about text could see, and the labels were
 * legible in the DOM while arriving on screen at about five pixels.
 *
 *   node scripts/ui-disruption-layout.mjs <baseUrl>
 */

import { chromium } from "playwright";
import { mkdirSync } from "node:fs";

const BASE = (process.argv[2] ?? "http://localhost:3111").replace(/\/+$/, "");
const SHOTS = "screenshots";
mkdirSync(SHOTS, { recursive: true });

const SIZES = [
  { width: 970, height: 666 },
  { width: 1280, height: 800 },
  { width: 1512, height: 900 },
];

const results = [];
function record(name, ok, detail = "") {
  results.push({ name, ok });
  console.log(
    `  ${ok ? "\x1b[32mPASS\x1b[0m" : "\x1b[31mFAIL\x1b[0m"}  ${name}${detail ? ` — ${detail}` : ""}`,
  );
}

const overlaps = (a, b) =>
  a.x < b.x + b.width && b.x < a.x + a.width && a.y < b.y + b.height && b.y < a.y + a.height;

const browser = await chromium.launch();

for (const size of SIZES) {
  const label = `${size.width}x${size.height}`;
  console.log(`\n${label}`);
  const page = await browser.newPage({ viewport: size });
  await page.goto(`${BASE}/disruption`, { waitUntil: "domcontentloaded" });
  await page.waitForSelector(".react-flow", { timeout: 30000 });
  await page.waitForTimeout(2500);

  // 1 — nothing may sit on top of the Event feed.
  const feed = await page
    .locator("section", { has: page.getByText("Event feed", { exact: true }) })
    .first()
    .boundingBox();
  const flow = await page.locator(".react-flow").first().boundingBox();
  record(
    `${label}: the graph does not overlap the Event feed`,
    feed && flow ? !overlaps(feed, flow) : false,
    feed && flow
      ? `feed y=${Math.round(feed.y)}..${Math.round(feed.y + feed.height)}, graph y=${Math.round(flow.y)}..${Math.round(flow.y + flow.height)}`
      : "could not measure",
  );

  // 2 — the graph stays inside the panel that owns it.
  const panel = await page
    .locator("section", { has: page.getByText("Dependency graph", { exact: true }) })
    .first()
    .boundingBox();
  record(
    `${label}: the graph stays inside its panel`,
    panel && flow ? flow.y + flow.height <= panel.y + panel.height + 1 : false,
    panel && flow
      ? `graph bottom ${Math.round(flow.y + flow.height)} vs panel bottom ${Math.round(panel.y + panel.height)}`
      : "",
  );

  // 3 — the page is reachable: either it fits, or it scrolls.
  const scroll = await page.evaluate(() => {
    const main = document.querySelector("main");
    return {
      docScrollable: document.documentElement.scrollHeight > window.innerHeight,
      mainScrollable: main ? main.scrollHeight > main.clientHeight : false,
      mainOverflow: main ? getComputedStyle(main).overflowY : "none",
      fits: main ? main.scrollHeight <= main.clientHeight : true,
    };
  });
  record(
    `${label}: nothing is unreachable`,
    scroll.fits || scroll.mainScrollable || scroll.docScrollable,
    scroll.fits ? "content fits" : `main overflow-y:${scroll.mainOverflow}, scrollable`,
  );

  // 4 — how much is drawn, and how big it actually lands on screen.
  const graph = await page.evaluate(() => {
    const nodes = [...document.querySelectorAll(".react-flow__node")];
    const viewport = document.querySelector(".react-flow__viewport");
    const t = viewport ? getComputedStyle(viewport).transform : "none";
    const scale = t && t !== "none" ? Number(t.split("(")[1].split(",")[0]) : 1;
    return {
      count: nodes.length,
      scale,
      sample: nodes[0]?.textContent?.trim() ?? "",
      fontPx: nodes[0] ? Number.parseFloat(getComputedStyle(nodes[0]).fontSize) : 0,
    };
  });
  const effective = graph.fontPx * graph.scale;
  record(
    `${label}: labels are legible at default zoom`,
    effective >= 8,
    `${graph.count} nodes, scale ${graph.scale.toFixed(2)}, ${graph.fontPx}px -> ${effective.toFixed(1)}px effective`,
  );
  record(`${label}: at rest the graph is collapsed`, graph.count <= 25, `${graph.count} nodes`);
  if (graph.sample) console.log(`        first node: "${graph.sample}"`);

  // 5 — the legend is present and names all three states.
  const legend = await page.locator("text=on track").first().isVisible().catch(() => false);
  const blocked = await page.locator("text=blocked").first().isVisible().catch(() => false);
  record(`${label}: the legend explains the colours`, legend && blocked);

  await page.screenshot({ path: `${SHOTS}/disruption-${label}.png`, fullPage: true });
  await page.close();
}

// ---------------------------------------------------------------------------
// A live disruption must expand the days it touches and leave the rest alone.
// ---------------------------------------------------------------------------
console.log("\nlive disruption at 1512x900");
{
  const page = await browser.newPage({ viewport: { width: 1512, height: 900 } });
  await page.goto(`${BASE}/disruption`, { waitUntil: "domcontentloaded" });
  await page.waitForSelector(".react-flow", { timeout: 30000 });
  await page.waitForTimeout(2500);

  const before = await page.evaluate(
    () => document.querySelectorAll(".react-flow__node").length,
  );

  await page.getByRole("button", { name: /Report a disruption/i }).click();
  await page.waitForTimeout(30000);

  const after = await page.evaluate(() => {
    const nodes = [...document.querySelectorAll(".react-flow__node")];
    const wide = nodes.filter((n) => {
      const w = getComputedStyle(n).borderTopWidth;
      return Number.parseFloat(w) >= 2;
    });
    return {
      count: nodes.length,
      thick: wide.length,
      labels: wide.map((n) => n.textContent?.trim()).slice(0, 6),
    };
  });

  record(
    "a live disruption expands the graph",
    after.count > before,
    `${before} nodes at rest -> ${after.count} live`,
  );
  record(
    "only some of the graph expands",
    after.count < before + 13,
    "scenes appear for affected days only, not for all thirteen",
  );
  record(
    "impacted nodes are drawn thicker",
    after.thick > 0,
    after.labels.join(", ") || "none",
  );
  const legend = await page.locator("text=affected days expanded").first().isVisible().catch(() => false);
  record("the legend says the graph expanded", legend);

  await page.screenshot({ path: `${SHOTS}/disruption-live.png`, fullPage: true });
  await page.close();
}

await browser.close();

const failed = results.filter((r) => !r.ok);
console.log(`\n${results.length - failed.length} passed, ${failed.length} failed`);
if (failed.length) {
  console.log("\nFailures:");
  for (const f of failed) console.log(`  - ${f.name}`);
}
process.exit(failed.length ? 1 : 0);
