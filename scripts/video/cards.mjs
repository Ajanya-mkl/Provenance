// Renders title cards, closing cards and caption overlays as PNGs (HTML in
// Chromium, with the console's own Inter font), for ffmpeg to overlay.
import { existsSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

import { chromium } from "playwright";

const HERE = dirname(fileURLToPath(import.meta.url));
const INTER = resolve(HERE, "../../web/node_modules/@fontsource-variable/inter/files/inter-latin-wght-normal.woff2");

const esc = (text) => text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");

function shell(width, height, body, { transparent = false } = {}) {
  const font = existsSync(INTER)
    ? `@font-face { font-family: Inter; src: url("${pathToFileURL(INTER).href}") format("woff2"); font-weight: 100 900; }`
    : "";
  return `<!doctype html><html><head><meta charset="utf-8"><style>
${font}
* { box-sizing: border-box; margin: 0; }
html, body { width: ${width}px; height: ${height}px; overflow: hidden; background: ${transparent ? "transparent" : "#0b1f22"}; }
body { font-family: Inter, "Segoe UI", sans-serif; color: #fff; -webkit-font-smoothing: antialiased; }
.bg { position: absolute; inset: 0; background:
  radial-gradient(1200px 700px at 85% 110%, rgba(45, 212, 191, .28), transparent 60%),
  radial-gradient(900px 600px at 0% -10%, rgba(56, 189, 248, .20), transparent 60%), #0b1f22; }
.mark { display: inline-flex; flex-direction: column; gap: 7px; }
.mark i { display: block; height: 9px; border-radius: 9px; }
.tab { font-variant-numeric: tabular-nums; }
</style></head><body>${body}</body></html>`;
}

const LOGO = (scale = 1) => `<span class="mark" style="transform: scale(${scale}); transform-origin: left center">
  <i style="width:46px;background:#2dd4bf"></i><i style="width:46px;background:#38bdf8"></i><i style="width:30px;background:#f59e0b"></i></span>`;

export const templates = {
  /** 1920x1080 opening card. */
  title: () =>
    shell(1920, 1080, `<div class="bg"></div>
    <div style="position:absolute;left:180px;top:0;bottom:0;display:flex;flex-direction:column;justify-content:center;gap:34px">
      <div style="display:flex;align-items:center;gap:64px">${LOGO(1.5)}
        <span style="font-size:132px;font-weight:750;letter-spacing:-.035em">Provenance</span></div>
      <div style="font-size:56px;font-weight:500;color:#cdeeea;letter-spacing:-.015em">Explainable supply chain risk intelligence</div>
      <div style="font-size:30px;color:#8fb7b3;margin-top:18px">A risk score that explains itself, and an AI agent that cannot act alone</div>
    </div>`),

  /** 1920x1080 closing card with the headline results. */
  closing: () => {
    const stats = [
      ["297", "unit tests pass"],
      ["16/16", "agent names the right top factor"],
      ["94.4%", "agent reports grounded (17 of 18)"],
      ["0", "unapproved actions"],
      ["211/211", "scores equal on the generic dataset path"],
    ];
    return shell(1920, 1080, `<div class="bg"></div>
    <div style="position:absolute;inset:0;display:flex;flex-direction:column;justify-content:center;padding:0 160px;gap:64px">
      <div style="display:flex;align-items:center;gap:24px">${LOGO(1.1)}<span style="font-size:72px;font-weight:750;letter-spacing:-.03em">Provenance</span></div>
      <div style="display:grid;grid-template-columns:repeat(5,1fr);gap:28px">
        ${stats.map(([n, l]) => `<div style="border-top:3px solid #2dd4bf;padding-top:26px">
          <div class="tab" style="font-size:76px;font-weight:750;letter-spacing:-.03em">${n}</div>
          <div style="font-size:27px;line-height:1.3;color:#cdeeea;margin-top:12px">${esc(l)}</div></div>`).join("")}
      </div>
      <div style="font-size:28px;color:#8fb7b3">DuckDB · Dagster · dbt · FastAPI · React · an LLM agent with human approval</div>
    </div>`);
  },

  /** 1920x1080 transparent overlay with a caption bar at the bottom. */
  caption: (text) =>
    shell(1920, 1080, `<div style="position:absolute;left:0;right:0;bottom:0;min-height:104px;padding:26px 120px;
      background:rgba(8,20,22,.80);display:flex;align-items:center;justify-content:center;text-align:center;
      font-size:36px;font-weight:600;letter-spacing:-.01em;line-height:1.25">${esc(text)}</div>`, { transparent: true }),

  /** 1080x1920 transparent overlay for the reel: big caption in the top band, brand line at the bottom. */
  reelCaption: (text, { hook = false, top = 470, bottom = 1500 } = {}) =>
    shell(1080, 1920, `
      <div style="position:absolute;left:70px;right:70px;top:0;height:${top}px;display:flex;align-items:flex-end;padding-bottom:44px">
        <div style="font-size:${hook ? 86 : 64}px;font-weight:${hook ? 800 : 700};line-height:1.12;letter-spacing:-.025em">${esc(text)}</div></div>
      <div style="position:absolute;left:70px;right:70px;top:${bottom}px;bottom:0;display:flex;align-items:center;gap:22px;
        font-size:40px;font-weight:650;color:#cdeeea">${LOGO(0.9)}<span>Provenance</span></div>`, { transparent: true }),

  /** 1080x1920 end card. */
  reelEnd: () =>
    shell(1080, 1920, `<div class="bg"></div>
    <div style="position:absolute;inset:0;display:flex;flex-direction:column;justify-content:center;padding:0 90px;gap:56px">
      <div style="display:flex;align-items:center;gap:44px">${LOGO(1.3)}<span style="font-size:108px;font-weight:780;letter-spacing:-.035em">Provenance</span></div>
      <div style="font-size:52px;font-weight:600;line-height:1.2;color:#cdeeea">Explainable supply chain risk intelligence</div>
      <div style="font-size:48px;line-height:1.3">PG Diploma in Data Science,<br>University of Calicut</div>
      <div style="font-size:44px;line-height:1.35;color:#8fb7b3">Built with DuckDB, Dagster, React and an AI agent with human approval</div>
    </div>`),
};

/** Render [{ file, html, width, height }] to PNGs. */
export async function renderPngs(items) {
  const browser = await chromium.launch();
  const page = await browser.newPage({ deviceScaleFactor: 1 });
  for (const item of items) {
    await page.setViewportSize({ width: item.width, height: item.height });
    await page.setContent(item.html, { waitUntil: "load" });
    await page.evaluate(() => document.fonts.ready);
    await page.screenshot({ path: item.file, omitBackground: true });
  }
  await browser.close();
}

export const out = (dir, name) => join(dir, `${name}.png`);
