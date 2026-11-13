// Screenshots every console page, to check layout before recording.
// Usage: node explore.mjs <outDir>
import { chromium } from "playwright";

const out = process.argv[2] ?? "explore";
const BASE = process.env.CONSOLE_URL ?? "http://localhost:5173";
const pages = [
  ["overview", "/"],
  ["flags", "/flags"],
  ["golf", "/flags?open=" + encodeURIComponent("risk_flag:1.2:2017-01-01:cat_golf_balls")],
  ["womens", "/flags/" + encodeURIComponent("risk_flag:1.2:2017-04-01:cat_women_s_apparel")],
  ["cardio", "/flags/" + encodeURIComponent("risk_flag:1.2:2017-04-01:cat_cardio_equipment")],
  ["approvals", "/approvals"],
  ["investigations", "/investigations"],
  ["pipeline", "/pipeline"],
  ["datasets", "/datasets"],
  ["mock-datasets", "/datasets?mock=1"],
];

const browser = await chromium.launch();
for (const [name, path] of pages) {
  const ctx = await browser.newContext({ viewport: { width: 1920, height: 1080 }, deviceScaleFactor: 1, colorScheme: "light" });
  const page = await ctx.newPage();
  await page.goto(BASE + path);
  await page.waitForTimeout(2500);
  await page.screenshot({ path: `${out}/${name}.png` });
  await page.screenshot({ path: `${out}/${name}-full.png`, fullPage: true });
  await ctx.close();
}
await browser.close();
