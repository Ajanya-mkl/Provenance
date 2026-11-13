// Builds docs/video/provenance_demo.mp4 and provenance_reel.mp4 from the scenes
// recorded by record.mjs, plus a contact sheet of each (a frame every 5 s).
//
//   FFMPEG=C:\path\to\ffmpeg.exe node edit.mjs          (or ffmpeg on PATH)
//   node edit.mjs demo | reel | sheets                   one part only
import { execFileSync } from "node:child_process";
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { renderPngs, templates } from "./cards.mjs";

const HERE = dirname(fileURLToPath(import.meta.url));
const DOCS = resolve(HERE, "../../docs/video");
const BUILD = resolve(process.env.VIDEO_BUILD ?? join(DOCS, "build"));
const RAW = join(BUILD, "raw");
const TMP = join(BUILD, "edit");
const FFMPEG = process.env.FFMPEG ?? "ffmpeg";
const FPS = 30;
const X264 = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", "-r", String(FPS), "-movflags", "+faststart"];
const ORDER = ["overview", "flags", "agent", "approvals", "memory", "datasets"];
const FADE = 0.35;
// Recorded scenes play slightly faster than real time to keep the demo under 4 minutes.
const SPEED = Number(process.env.DEMO_SPEED ?? 1.1);

mkdirSync(TMP, { recursive: true });
const ff = (args) => execFileSync(FFMPEG, ["-hide_banner", "-loglevel", "error", "-y", ...args], { stdio: "inherit" });
const probe = (file) =>
  Number(
    execFileSync(FFMPEG, ["-hide_banner", "-i", file], { encoding: "utf8", stdio: ["ignore", "pipe", "pipe"] })
      .toString()
      .match(/Duration: (\d+):(\d+):([\d.]+)/)
      ?.slice(1)
      .reduce((acc, v) => acc * 60 + Number(v), 0) ?? 0,
  );
const duration = (file) => {
  try {
    return probe(file);
  } catch (error) {
    // ffmpeg -i with no output exits 1 but still prints the duration on stderr.
    const m = String(error.stderr).match(/Duration: (\d+):(\d+):([\d.]+)/);
    return m ? Number(m[1]) * 3600 + Number(m[2]) * 60 + Number(m[3]) : 0;
  }
};
const f3 = (n) => n.toFixed(3);

function loadScene(name) {
  const meta = JSON.parse(readFileSync(join(RAW, `${name}.json`), "utf8"));
  const start = Math.max(0, (meta.marks.start?.t ?? 0) - 0.2);
  const end = meta.duration - 0.4;
  // Segments of source time, each with a speed. Only the agent wait is sped up.
  let segments = [{ from: start, to: end, speed: SPEED }];
  const a = meta.marks.agent_start?.t;
  const d = meta.marks.agent_done?.t;
  if (a != null && d != null && d - a > 3.2) {
    const keepHead = 1.0;
    const keepTail = 0.6;
    const middle = d - keepTail - (a + keepHead);
    const target = 1.4; // ~3 s of waiting on screen in all
    segments = [
      { from: start, to: a + keepHead, speed: SPEED },
      { from: a + keepHead, to: d - keepTail, speed: Math.max(1, middle / target) },
      { from: d - keepTail, to: end, speed: SPEED },
    ];
  }
  // Map a source time to edited time.
  const map = (t) => {
    let out = 0;
    for (const seg of segments) {
      if (t <= seg.from) break;
      out += (Math.min(t, seg.to) - seg.from) / seg.speed;
    }
    return out;
  };
  const length = map(end);
  return { name, meta, segments, map, length, src: join(RAW, `${name}.webm`) };
}

/** Trim, warp and (optionally) caption one scene into an mp4. */
function renderScene(scene, { captions }) {
  const inputs = ["-i", scene.src];
  const parts = scene.segments.map(
    (seg, i) =>
      `[0:v]trim=start=${f3(seg.from)}:end=${f3(seg.to)},setpts=(PTS-STARTPTS)/${f3(seg.speed)},fps=${FPS}[s${i}]`,
  );
  let graph = parts.join(";") + ";" + scene.segments.map((_, i) => `[s${i}]`).join("") + `concat=n=${scene.segments.length}:v=1:a=0[base]`;
  let last = "base";
  if (captions) {
    const cues = scene.meta.cues;
    cues.forEach((cue, i) => {
      const from = scene.map(cue.t);
      const to = i + 1 < cues.length ? scene.map(cues[i + 1].t) : scene.length;
      if (to - from < 0.3) return;
      inputs.push("-i", cue.png);
      const idx = inputs.filter((x) => x === "-i").length - 1;
      graph += `;[${last}][${idx}:v]overlay=0:0:enable='between(t,${f3(from)},${f3(to)})'[c${i}]`;
      last = `c${i}`;
    });
  }
  graph += `;[${last}]fade=t=in:st=0:d=${FADE},fade=t=out:st=${f3(scene.length - FADE)}:d=${FADE},format=yuv420p[out]`;
  const file = join(TMP, `${scene.name}${captions ? "" : "_clean"}.mp4`);
  ff([...inputs, "-filter_complex", graph, "-map", "[out]", "-t", f3(scene.length), ...X264, file]);
  return file;
}

function stillClip(png, seconds, file, { width = 1920, height = 1080 } = {}) {
  ff([
    "-loop", "1", "-framerate", String(FPS), "-t", String(seconds), "-i", png,
    "-vf", `scale=${width}:${height},fade=t=in:st=0:d=0.5,fade=t=out:st=${seconds - 0.5}:d=0.5,format=yuv420p`,
    ...X264, file,
  ]);
  return file;
}

function concat(files, file) {
  const list = join(TMP, `${file.split(/[\\/]/).pop()}.txt`);
  writeFileSync(list, files.map((f) => `file '${f.replace(/\\/g, "/")}'`).join("\n"));
  ff(["-f", "concat", "-safe", "0", "-i", list, "-c", "copy", "-movflags", "+faststart", file]);
}

async function buildDemo(scenes) {
  const pngs = [
    { file: join(TMP, "title.png"), html: templates.title(), width: 1920, height: 1080 },
    { file: join(TMP, "closing.png"), html: templates.closing(), width: 1920, height: 1080 },
  ];
  for (const scene of scenes) {
    scene.meta.cues.forEach((cue, i) => {
      cue.png = join(TMP, `cap_${scene.name}_${i}.png`);
      pngs.push({ file: cue.png, html: templates.caption(cue.text), width: 1920, height: 1080 });
    });
  }
  await renderPngs(pngs);
  const files = [stillClip(pngs[0].file, 4, join(TMP, "title.mp4"))];
  for (const scene of scenes) files.push(renderScene(scene, { captions: true }));
  files.push(stillClip(pngs[1].file, 6, join(TMP, "closing.mp4")));
  const out = join(DOCS, "provenance_demo.mp4");
  concat(files, out);
  console.log(`demo: ${out} (${duration(out).toFixed(1)} s)`);
}

// ---- Reel (1080x1920) ----
const REEL = { w: 1080, h: 1920, top: 470, region: 1030 };

/** A crop of the 1920x1080 frame around `box`, with the reel region's aspect. */
function cropFor(box, { minW = 760, maxW = 1500, pad = 50 } = {}) {
  const aspect = REEL.w / REEL.region;
  let w = Math.max(minW, Math.min(maxW, Math.max(box.width + pad * 2, (box.height + pad * 2) * aspect)));
  let h = w / aspect;
  if (h > 1080) {
    h = 1080;
    w = h * aspect;
  }
  const cx = box.x + box.width / 2;
  const cy = box.y + Math.min(box.height, h - pad * 2) / 2;
  // A card wider than the crop is left-aligned, so its title and first column stay in view.
  const left = box.width + pad * 2 > w ? box.x - pad : cx - w / 2;
  const x = Math.min(Math.max(0, left), 1920 - w);
  const y = Math.min(Math.max(0, cy - h / 2), 1080 - h);
  const even = (n) => Math.round(n / 2) * 2;
  return { x: even(x), y: even(y), w: even(w), h: even(h) };
}

async function buildReel(scenes) {
  const by = Object.fromEntries(scenes.map((s) => [s.name, s]));
  const clean = Object.fromEntries(scenes.map((s) => [s.name, join(TMP, `${s.name}_clean.mp4`)]));
  for (const s of scenes) renderScene(s, { captions: false });
  const at = (scene, key) => by[scene].map(by[scene].meta.marks[key].t);
  const box = (scene, key, fallback) => by[scene].meta.marks[key]?.box ?? fallback;

  const clips = [
    { text: "Most risk scores can't explain themselves. Mine does.", hook: true, scene: "flags", from: at("flags", "reel_flag_start") + 0.6, len: 4.6,
      crop: cropFor({ x: 1216, y: 100, width: 690, height: 300 }, { minW: 700 }) },
    { text: "Every flag shows the factors behind its score", scene: "flags", from: at("flags", "reel_factors_start"), len: 4.6,
      crop: cropFor(box("flags", "reel_factors_box", { x: 1216, y: 300, width: 690, height: 300 }), { minW: 700 }) },
    { text: "An AI agent investigates, using read-only tools", scene: "agent", from: at("agent", "reel_agent_start") - 0.4, len: 4.4,
      crop: cropFor(box("agent", "reel_agent_box", { x: 456, y: 360, width: 1264, height: 320 }), { minW: 900 }) },
    { text: "GROUNDED: every number traces to a tool result", scene: "agent", from: at("agent", "reel_grounded_start") - 0.3, len: 5,
      crop: cropFor(box("agent", "reel_grounded_box", { x: 456, y: 360, width: 1264, height: 320 }), { minW: 900 }) },
    { text: "Nothing happens until a named human approves", scene: "approvals", from: at("approvals", "reel_approve_start") + 0.3, len: 5,
      crop: cropFor(box("approvals", "reel_approve_box", { x: 660, y: 300, width: 600, height: 480 })) },
    { text: "Bad batches are quarantined, not scored", scene: "memory", from: at("memory", "reel_quarantine_start"), len: 4,
      crop: cropFor(box("memory", "reel_quarantine_box", { x: 456, y: 300, width: 860, height: 470 }), { minW: 900 }) },
    { text: "And every decision is in a hash-chained audit log", scene: "memory", from: at("memory", "reel_audit_start"), len: 4.4,
      crop: cropFor(box("memory", "reel_audit_box", { x: 1340, y: 140, width: 380, height: 190 }), { minW: 820 }) },
  ];

  const pngs = clips.map((clip, i) => ({
    file: join(TMP, `reel_cap_${i}.png`),
    html: templates.reelCaption(clip.text, { hook: clip.hook, top: REEL.top, bottom: REEL.top + REEL.region }),
    width: REEL.w,
    height: REEL.h,
  }));
  pngs.push({ file: join(TMP, "reel_end.png"), html: templates.reelEnd(), width: REEL.w, height: REEL.h });
  await renderPngs(pngs);

  const files = clips.map((clip, i) => {
    const { x, y, w, h } = clip.crop;
    const file = join(TMP, `reel_${i}.mp4`);
    const graph = [
      `color=c=0x0b1f22:s=${REEL.w}x${REEL.h}:r=${FPS}:d=${f3(clip.len)}[bg]`,
      `[0:v]crop=${w}:${h}:${x}:${y},scale=${REEL.w}:${REEL.region}:flags=lanczos,setsar=1[v]`,
      `[bg][v]overlay=0:${REEL.top}[a]`,
      `[a][1:v]overlay=0:0,fade=t=in:st=0:d=${i === 0 ? 0 : 0.25},fade=t=out:st=${f3(clip.len - 0.25)}:d=0.25,format=yuv420p[out]`,
    ].join(";");
    ff(["-ss", f3(Math.max(0, clip.from)), "-t", f3(clip.len), "-i", clean[clip.scene], "-i", pngs[i].file,
      "-filter_complex", graph, "-map", "[out]", "-t", f3(clip.len), ...X264, file]);
    return file;
  });
  files.push(stillClip(pngs.at(-1).file, 3, join(TMP, "reel_end.mp4"), { width: REEL.w, height: REEL.h }));
  const out = join(DOCS, "provenance_reel.mp4");
  concat(files, out);
  console.log(`reel: ${out} (${duration(out).toFixed(1)} s)`);
}

function contactSheet(video, name, { cols, width }) {
  const secs = duration(video);
  const n = Math.ceil(secs / 5);
  const rows = Math.ceil(n / cols);
  const out = join(BUILD, `${name}_contact_sheet.png`);
  ff(["-i", video, "-vf",
    `fps=1/5,scale=${width}:-2,drawtext=fontfile='C\\:/Windows/Fonts/arial.ttf':text='%{pts\\:hms}':x=8:y=8:fontsize=18:fontcolor=white:box=1:boxcolor=black@0.6,tile=${cols}x${rows}:padding=6:margin=6:color=0x222222`,
    "-frames:v", "1", "-update", "1", out]);
  console.log(`contact sheet: ${out} (${n} frames)`);
}

const part = process.argv[2] ?? "all";
const scenes = ORDER.filter((n) => existsSync(join(RAW, `${n}.json`))).map(loadScene);
const missing = ORDER.filter((n) => !scenes.find((s) => s.name === n));
if (missing.length) console.warn(`missing scenes: ${missing.join(", ")}`);
if (part === "all" || part === "demo") await buildDemo(scenes);
if (part === "all" || part === "reel") await buildReel(scenes);
if (part === "all" || part === "sheets") {
  contactSheet(join(DOCS, "provenance_demo.mp4"), "demo", { cols: 6, width: 400 });
  contactSheet(join(DOCS, "provenance_reel.mp4"), "reel", { cols: 9, width: 216 });
}
