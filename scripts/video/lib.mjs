// Shared helpers for recording the Provenance console with Playwright.
// Nothing here changes the app: the visible cursor is an overlay injected into
// the recorded page only, so viewers can follow the pointer.
import { mkdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { chromium } from "playwright";

export const BASE = process.env.CONSOLE_URL ?? "http://localhost:5173";
export const VIEWPORT = { width: 1920, height: 1080 };
/** Pause after every action, in place of Playwright's slowMo (which would also slow each glide step). */
export const ACTION_PAUSE = 400;

const CURSOR_SCRIPT = `
(() => {
  if (window.__videoCursor) return;
  window.__videoCursor = true;
  const install = () => {
    const c = document.createElement('div');
    c.id = '__video_cursor';
    c.innerHTML = '<svg width="28" height="28" viewBox="0 0 24 24"><path d="M4 2l15 11.5-6.6 1.1 3.9 7.4-3 1.5-3.9-7.5L4 21z" fill="#111" stroke="#fff" stroke-width="1.4" stroke-linejoin="round"/></svg>';
    Object.assign(c.style, { position: 'fixed', left: '0', top: '0', zIndex: 2147483647, pointerEvents: 'none',
      transform: 'translate(-100px,-100px)', filter: 'drop-shadow(0 2px 3px rgba(0,0,0,.35))' });
    document.documentElement.appendChild(c);
    const ring = document.createElement('div');
    Object.assign(ring.style, { position: 'fixed', left: '0', top: '0', width: '40px', height: '40px', margin: '-20px 0 0 -20px',
      borderRadius: '50%', border: '3px solid rgba(13,148,136,.85)', zIndex: 2147483646, pointerEvents: 'none', opacity: '0',
      transition: 'opacity .45s, transform .45s' });
    document.documentElement.appendChild(ring);
    // The console's dialogs use showModal(), which paints in the top layer above
    // any z-index. Make the cursor a manual popover (also top layer) and re-show
    // it whenever a dialog opens, so it is always painted last.
    const reset = { inset: 'auto', margin: '0', padding: '0', border: 'none', background: 'transparent', overflow: 'visible' };
    for (const el of [ring, c]) { el.setAttribute('popover', 'manual'); Object.assign(el.style, reset, { left: '0', top: '0' }); }
    ring.style.margin = '-20px 0 0 -20px'; ring.style.border = '3px solid rgba(13,148,136,.85)';
    const raise = () => { for (const el of [ring, c]) { try { if (el.matches(':popover-open')) el.hidePopover(); el.showPopover(); } catch {} } };
    raise();
    new MutationObserver(() => setTimeout(raise, 30)).observe(document.documentElement, { subtree: true, attributes: true, attributeFilter: ['open'] });
    addEventListener('mousemove', (e) => { c.style.transform = 'translate(' + (e.clientX - 4) + 'px,' + (e.clientY - 2) + 'px)'; }, true);
    addEventListener('mousedown', (e) => {
      ring.style.transition = 'none'; ring.style.opacity = '1';
      ring.style.transform = 'translate(' + e.clientX + 'px,' + e.clientY + 'px) scale(.4)';
      requestAnimationFrame(() => { ring.style.transition = 'opacity .45s, transform .45s'; ring.style.opacity = '0';
        ring.style.transform = 'translate(' + e.clientX + 'px,' + e.clientY + 'px) scale(1.4)'; });
    }, true);
  };
  if (document.documentElement) install(); else addEventListener('DOMContentLoaded', install);
})();`;

export async function launch() {
  // GPU flags: in plain headless mode the console's glass blur renders in software
  // and each pointer step takes ~400 ms, which makes recordings drag.
  return chromium.launch({
    headless: true,
    args: ["--enable-gpu", "--use-angle=d3d11", "--ignore-gpu-blocklist", "--enable-unsafe-swiftshader"],
  });
}

/**
 * One scene = one browser context = one video file. Records caption cues and
 * named marks (seconds from the start of the video) to <name>.json for the editor.
 */
export async function startScene(browser, outDir, name, { mock = false } = {}) {
  mkdirSync(outDir, { recursive: true });
  const context = await browser.newContext({
    viewport: VIEWPORT,
    deviceScaleFactor: 1,
    colorScheme: "light",
    locale: "en-GB",
    timezoneId: "Asia/Kolkata",
    recordVideo: { dir: join(outDir, `_tmp_${name}`), size: VIEWPORT },
  });
  await context.addInitScript(CURSOR_SCRIPT);
  const page = await context.newPage();
  const t0 = Date.now();
  const log = { scene: name, mock, cues: [], marks: {} };
  let pos = { x: 960, y: 540 };

  const now = () => (Date.now() - t0) / 1000;
  const pause = (ms) => page.waitForTimeout(ms);

  const scene = {
    page,
    mock,
    /** Navigate to a console path (adds ?mock=1 in mock scenes) and wait for the network to settle. */
    async go(path) {
      const url = new URL(BASE + path);
      if (mock) url.searchParams.set("mock", "1");
      await page.goto(url.toString(), { waitUntil: "networkidle" });
      await page.mouse.move(pos.x, pos.y);
    },
    /** Start a caption from now until the next cue. */
    cue(text) {
      log.cues.push({ t: now(), text });
    },
    mark(key, extra = {}) {
      log.marks[key] = { t: now(), ...extra };
    },
    /** Record an element's box for the reel's crop. */
    async markBox(key, locator) {
      const t = now();
      const box = await locator.boundingBox({ timeout: 3000 }).catch(() => null);
      log.marks[key] = { t, box };
    },
    pause,
    async glide(x, y, steps = 16) {
      await page.mouse.move(x, y, { steps });
      pos = { x, y };
    },
    async hover(locator, { dwell = 1200 } = {}) {
      await locator.scrollIntoViewIfNeeded();
      const box = await locator.boundingBox();
      if (!box) throw new Error(`hover: no box for ${locator}`);
      await scene.glide(box.x + box.width / 2, box.y + box.height / 2);
      await pause(dwell);
    },
    async click(locator, { dwell = ACTION_PAUSE } = {}) {
      await locator.waitFor({ state: "visible" });
      await locator.scrollIntoViewIfNeeded();
      const box = await locator.boundingBox();
      await scene.glide(box.x + box.width / 2, box.y + box.height / 2);
      await pause(250);
      await page.mouse.down();
      await page.mouse.up();
      await pause(dwell);
    },
    async type(locator, text) {
      await scene.click(locator);
      await locator.pressSequentially(text, { delay: 140 });
      await pause(ACTION_PAUSE);
    },
    /** Smooth scroll of the page (or the element under the pointer) by dy pixels. */
    async scroll(dy, { steps = 12 } = {}) {
      const step = dy / steps;
      for (let i = 0; i < steps; i += 1) {
        await page.mouse.wheel(0, step);
        await pause(45);
      }
      await pause(ACTION_PAUSE);
    },
    async end() {
      log.duration = now();
      const video = page.video();
      await context.close();
      const tmp = await video.path();
      const final = join(outDir, `${name}.webm`);
      await video.saveAs(final);
      await video.delete();
      writeFileSync(join(outDir, `${name}.json`), JSON.stringify(log, null, 2));
      void tmp;
      return final;
    },
  };
  return scene;
}
