// Records the demo scenes from the running console, one .webm + .json per scene.
//
//   node record.mjs                 all scenes
//   node record.mjs agent approvals only these scenes
//
// Needs the API (python -m provenance.api) on a COPY of the warehouse and
// `npm run dev` in web/ (see docs/video/README.md). The agent scene makes one
// real LLM call; if it fails or takes longer than 40 s it is re-recorded in
// mock mode (?mock=1) and the scene's .json says so.
import { existsSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { BASE, launch, startScene } from "./lib.mjs";

const HERE = dirname(fileURLToPath(import.meta.url));
const OUT = resolve(process.env.VIDEO_BUILD ?? join(HERE, "../../docs/video/build"), "raw");
const REVIEWER = process.env.VIDEO_REVIEWER ?? "Ajanya";
const AGENT_TIMEOUT_MS = Number(process.env.VIDEO_AGENT_TIMEOUT_S ?? 40) * 1000;

const id = (event) => encodeURIComponent(event);
const GOLF = "risk_flag:1.2:2017-01-01:cat_golf_balls";
const BOXING = "risk_flag:1.2:2017-07-01:cat_boxing_mma:p7f1b3f8080";
// Women's Apparel 2017-Q2 already has a grounded report in the warehouse, so the
// console shows that report and no button. The live run uses Cardio Equipment
// 2017-Q2 (same quarter, not investigated yet).
const LIVE = "risk_flag:1.2:2017-04-01:cat_cardio_equipment";
const WOMENS = "risk_flag:1.2:2017-04-01:cat_women_s_apparel";

const SCENES = {
  async overview(s) {
    await s.go("/");
    await s.page.getByText("Grounded rate").first().waitFor();
    await s.pause(800);
    s.mark("start");
    s.cue("Overview: 20 risk flags from a rule-based score, across 7 quarters");
    await s.hover(s.page.getByText(/risk flags across/), { dwell: 1700 });
    await s.hover(s.page.getByText("Grounded rate").first(), { dwell: 1800 });
    const agent = s.page.getByRole("heading", { name: "Agent", exact: true });
    s.cue("The Agent card: it explains flags and proposes actions. Nothing takes effect without a named human.");
    await s.markBox("reel_overview_agent", agent.locator("xpath=ancestor::section[1]"));
    await s.hover(agent, { dwell: 1500 });
    await s.hover(s.page.getByText("Proposals awaiting approval"), { dwell: 1800 });
    await s.glide(1500, 700);
    s.cue("Recent activity comes straight from the hash-chained audit log");
    await s.scroll(380);
    await s.hover(s.page.getByRole("heading", { name: "Recent activity" }), { dwell: 3000 });
  },

  async flags(s) {
    await s.go("/flags");
    const golf = s.page.getByRole("button", { name: /Golf Balls/ }).first();
    await golf.waitFor();
    await s.pause(600);
    s.mark("start");
    s.cue("Flags: each one is a supplier-quarter tier change from the rule engine");
    await s.glide(1000, 400);
    await s.pause(1400);
    s.cue("Golf Balls, 2017-Q1: Medium to High");
    await s.click(golf, { dwell: 1500 });
    const sheet = s.page.getByRole("dialog");
    await sheet.getByText("risk score of 1").waitFor();
    s.mark("reel_flag_start");
    await s.markBox("reel_flag_box", sheet);
    s.cue("The score is 0.735, and the sheet says in plain words which factor drove it");
    await s.hover(sheet.getByText("risk score of 1"), { dwell: 1600 });
    await s.hover(sheet.getByText(/The pipeline names/), { dwell: 2000 });
    s.cue("Three weighted factors add up to the score: delay variability, late deliveries, country risk");
    const breakdown = sheet.getByRole("heading", { name: "Score breakdown" });
    await s.glide(1550, 700);
    await s.scroll(420);
    s.mark("reel_factors_start");
    await s.markBox("reel_factors_box", breakdown.locator("xpath=ancestor::section[1]"));
    await s.hover(sheet.getByText("Delay variability").last(), { dwell: 1300 });
    await s.hover(sheet.getByText("Late-delivery rate").last(), { dwell: 1000 });
    await s.hover(sheet.getByText("Destination country risk").last(), { dwell: 1500 });
    s.mark("reel_factors_end");
    s.cue("Supplier history: SCD2 versions of the supplier's risk bands");
    await s.scroll(900);
    await s.hover(sheet.getByRole("heading", { name: "Supplier history" }), { dwell: 2200 });
    await s.click(s.page.getByRole("button", { name: "Close details" }), { dwell: 900 });
    s.cue("Uncertainty is shown, not hidden: filter to low-confidence flags");
    await s.click(s.page.getByRole("radiogroup", { name: "Confidence" }).getByText("Low", { exact: true }), { dwell: 1500 });
    const boxing = s.page.getByRole("button", { name: /Boxing & MMA/ }).first();
    s.cue("Boxing & MMA, 2017-Q3: Low confidence, because the quarter has too little data");
    await s.click(boxing, { dwell: 1200 });
    await s.page.getByRole("dialog").getByText("Low confidence").first().waitFor();
    await s.hover(s.page.getByRole("dialog").getByText("Low confidence").first(), { dwell: 2800 });
  },

  async agent(s) {
    // A quick look at an existing grounded report, then a live run.
    await s.go(`/flags/${id(LIVE)}`);
    const button = s.page.getByRole("button", { name: "Investigate with agent" });
    const live = await button.waitFor({ timeout: 15_000 }).then(() => true, () => false);
    if (!live) throw new Error("Cardio Equipment already has a report: record on a fresh copy of the warehouse.");
    await s.pause(600);
    s.mark("start");
    s.cue("Cardio Equipment, 2017-Q2: flagged, not investigated yet");
    await s.hover(s.page.getByText("The pipeline names"), { dwell: 2200 });
    s.cue(s.mock ? "Investigate with agent (mock replay: the live call failed or took over 40 s)" : "Investigate with agent: a live LLM call with read-only tools");
    s.mark("reel_agent_start");
    await s.markBox("reel_agent_box", s.page.getByRole("heading", { name: "Agent investigation" }).locator("xpath=ancestor::section[1]"));
    await s.click(button, { dwell: 300 });
    s.mark("agent_start");
    s.cue(s.mock ? "Waiting for the agent (sped up; mock replay, as the live call failed or took over 40 s)" : "Waiting for the agent (sped up)");
    // The badge reads "GROUNDED · every number traces…", so match its start, case-sensitively.
    const badge = s.page.getByText(/^GROUNDED/).first();
    const started = Date.now();
    await badge.waitFor({ timeout: AGENT_TIMEOUT_MS });
    s.mark("agent_done", { seconds: (Date.now() - started) / 1000 });
    s.cue("GROUNDED: every number in the report traces to a tool result");
    await s.pause(1800);
    await s.hover(badge, { dwell: 1500 });
    s.mark("reel_grounded_start");
    await s.markBox("reel_grounded_box", s.page.getByRole("heading", { name: "Agent investigation" }).locator("xpath=ancestor::section[1]"));
    const chips = s.page.locator('button[aria-describedby]').filter({ hasText: /\d/ });
    const count = Math.min(await chips.count(), 3);
    s.cue("Hover a number: it names the tool that returned it");
    for (let i = 0; i < count; i += 1) await s.hover(chips.nth(i), { dwell: 1600 });
    s.mark("reel_grounded_end");
    s.cue("The agent only proposes an action. It waits for a named human.");
    await s.hover(s.page.getByText(/^Proposed:/), { dwell: 3000 });
  },

  async approvals(s) {
    await s.go("/approvals");
    await s.page.getByRole("heading", { name: "Approvals" }).waitFor();
    await s.page.getByRole("button", { name: /Approve/ }).first().waitFor();
    await s.pause(600);
    s.mark("start");
    s.cue("Approvals: nothing the agent proposes takes effect until a named reviewer approves it");
    await s.glide(1100, 450);
    await s.pause(2000);
    const rows = s.page.locator("section[aria-label='Action queue'] li");
    let row = rows.filter({ hasText: "Cardio Equipment" }).first();
    if (!(await row.count())) row = rows.filter({ has: s.page.getByRole("button", { name: /Approve/ }) }).first();
    s.cue("Approve by name. The decision is recorded against it in the audit log.");
    await s.click(row.getByRole("button", { name: /Approve/ }), { dwell: 900 });
    const dialog = s.page.getByRole("dialog");
    s.mark("reel_approve_start");
    await s.markBox("reel_approve_box", dialog);
    await s.type(dialog.getByLabel(/Reviewer name/), REVIEWER);
    await s.pause(900);
    await s.click(dialog.getByRole("button", { name: "Approve and apply" }), { dwell: 600 });
    await s.page.getByText(/Approved and applied|Approved\. No action/).first().waitFor({ timeout: 30_000 });
    s.mark("reel_approve_end");
    s.cue("Approved and applied, by name");
    await s.pause(2600);
    s.cue("Decided: who approved what, and when");
    await s.click(s.page.getByRole("radiogroup", { name: "Show" }).getByText("Decided"), { dwell: 1200 });
    await s.hover(s.page.getByText(`by ${REVIEWER}`).first(), { dwell: 3200 });
  },

  async memory(s) {
    await s.go("/investigations");
    await s.page.getByRole("heading", { name: "Investigations" }).waitFor();
    await s.pause(800);
    s.mark("start");
    s.cue("Investigations: every report is kept, with the human decision on it");
    await s.glide(1000, 300);
    await s.pause(2200);
    const cleats = s.page.getByRole("link", { name: /Cleats/ }).first();
    s.cue("Later runs on the same supplier recall these earlier cases and decisions");
    await s.click(cleats, { dwell: 1500 });
    await s.page.getByText("Sources").first().waitFor();
    await s.hover(s.page.getByText("Sources").first(), { dwell: 1300 });
    await s.glide(1200, 650);
    await s.scroll(500);
    await s.pause(1400);
    s.cue("Pipeline: the quality gate checks every batch before it is used");
    await s.go("/pipeline");
    await s.page.getByText("Intact").waitFor({ timeout: 30_000 });
    await s.pause(600);
    await s.hover(s.page.getByRole("heading", { name: "Quality gate history" }), { dwell: 1200 });
    s.cue("A batch that fails a hard rule is quarantined, and the rest keep flowing");
    s.mark("reel_quarantine_start");
    await s.markBox("reel_quarantine_box", s.page.getByRole("heading", { name: "Quality gate history" }).locator("xpath=ancestor::section[1]"));
    await s.hover(s.page.getByText(/quarantined/).first(), { dwell: 3000 });
    s.mark("reel_quarantine_end");
    s.cue("Audit chain: Intact. Each row hashes the previous one, so an edited row breaks it.");
    const chain = s.page.getByRole("heading", { name: "Audit chain" }).locator("xpath=ancestor::section[1]");
    s.mark("reel_audit_start");
    await s.markBox("reel_audit_box", chain);
    await s.hover(s.page.getByText("Intact"), { dwell: 2200 });
    await s.click(s.page.getByRole("button", { name: /Verify again/ }), { dwell: 1800 });
    s.mark("reel_audit_end");
  },

  async datasets(s) {
    await s.go("/datasets");
    await s.page.getByRole("link", { name: "Northwind regional shipments" }).waitFor();
    await s.pause(600);
    s.mark("start");
    s.cue("Datasets and pipelines: bring your own shipment file (sample data shown)");
    const steps = s.page.locator("ol[aria-label='How it works'] > li");
    for (let i = 0; i < 4; i += 1) await s.hover(steps.nth(i), { dwell: i === 3 ? 1200 : 700 });
    s.cue("Upload: the file is profiled and checked by quality rules generated from its own columns");
    await s.click(s.page.getByRole("link", { name: /Upload dataset/ }), { dwell: 1000 });
    await s.page.locator('input[type="file"]').setInputFiles(process.env.VIDEO_SAMPLE_CSV);
    await s.pause(1200);
    await s.click(s.page.getByRole("button", { name: "Upload and check" }), { dwell: 600 });
    await s.page.getByText("Review mapping", { exact: true }).waitFor();
    await s.pause(1800);
    s.cue("Map: a heuristic matches the columns first; the agent is asked only when it is unsure");
    await s.click(s.page.getByText("Review mapping", { exact: true }).first(), { dwell: 800 });
    await s.type(s.page.getByLabel(/Reviewer name/), REVIEWER);
    await s.click(s.page.getByRole("button", { name: "Propose a mapping" }), { dwell: 600 });
    await s.page.getByText(/Decided by/i).first().waitFor({ timeout: 15_000 }).catch(() => undefined);
    await s.pause(1200);
    const decided = s.page.getByText(/Decided by/i).first();
    if (await decided.count()) await s.hover(decided, { dwell: 2200 });
    else await s.pause(2200);
    s.cue("Results: no country column, so country risk is left out and the other weights are renormalised");
    await s.go("/datasets/83384e3e01b2/results");
    await s.page.getByRole("heading", { name: "Factors and weights" }).waitFor();
    await s.pause(600);
    await s.hover(s.page.getByText(/Excluded/).first(), { dwell: 3000 });
  },
};

async function recordScene(browser, name, opts = {}) {
  const s = await startScene(browser, OUT, name, opts);
  try {
    await SCENES[name](s);
    await s.pause(1200);
    const file = await s.end();
    console.log(`recorded ${name}${opts.mock ? " (mock)" : ""} -> ${file}`);
    return true;
  } catch (error) {
    console.error(`scene ${name} failed: ${error.message}`);
    await s.end().catch(() => undefined);
    return false;
  }
}

const MOCK_ONLY = new Set(["datasets"]);
const wanted = process.argv.slice(2).length ? process.argv.slice(2) : Object.keys(SCENES);
if (wanted.includes("datasets") && !(process.env.VIDEO_SAMPLE_CSV && existsSync(process.env.VIDEO_SAMPLE_CSV))) {
  throw new Error("Set VIDEO_SAMPLE_CSV to a shipment CSV for the upload scene (see docs/video/README.md).");
}
console.log(`console: ${BASE}  out: ${OUT}`);
const browser = await launch();
for (const name of wanted) {
  if (!SCENES[name]) throw new Error(`unknown scene ${name}`);
  const ok = await recordScene(browser, name, { mock: MOCK_ONLY.has(name) });
  if (!ok && name === "agent") {
    console.log("agent scene: falling back to mock mode for this scene only");
    await recordScene(browser, name, { mock: true });
  } else if (!ok) {
    process.exitCode = 1;
  }
}
await browser.close();
void WOMENS;
