# Demo videos

Two videos recorded from the real console with Playwright and edited with ffmpeg. No pipeline, agent or console code is involved beyond running it: the scripts live in [scripts/video/](../../scripts/video/).

| File | What | Format |
|---|---|---|
| `provenance_demo.mp4` | Demo, about 4 minutes, captions burned in, no voiceover | 1920x1080, H.264, CRF 20 |
| `provenance_reel.mp4` | LinkedIn reel, about 35 seconds, big captions for muted viewing | 1080x1920, H.264, CRF 20 |
| [linkedin_post.txt](linkedin_post.txt) | Caption for the post | text |

The `.mp4` files and `build/` are git-ignored. Do not commit them.

## Re-record

Windows, from the repository root, after the [README](../../README.md#install-and-check) install.

**1. Tools, once.** Playwright and Chromium for the recorder; ffmpeg for editing.

```bat
cd scripts\video
npm install
npx playwright install chromium
winget install Gyan.FFmpeg
```

Without winget: `pip install --target %TEMP%\ffm imageio-ffmpeg` gives a portable `ffmpeg-win-x86_64-*.exe` under `%TEMP%\ffm\imageio_ffmpeg\binaries\`; point `FFMPEG` at it (step 4).

**2. A fresh copy of the warehouse.** The agent scene investigates Cardio Equipment 2017-Q2 and the approvals scene approves it, so each recording needs a copy on which that flag has not been investigated. The real warehouse is never touched.

```bat
rmdir /s /q C:\temp\prov-video
mkdir C:\temp\prov-video
copy data\provenance.duckdb C:\temp\prov-video\provenance.duckdb
set PROVENANCE_DUCKDB_PATH=C:\temp\prov-video\provenance.duckdb
set PROVENANCE_DATASETS_ROOT=C:\temp\prov-video\datasets
set PROVENANCE_DATASETS_QUARANTINE_ROOT=C:\temp\prov-video\quarantine
.venv\Scripts\activate
python -m provenance.api
```

In a second window: `cd web` then `npm run dev` (real mode). `LLM_API_KEY` must be in `.env` for the live agent call. No secrets appear on screen: the recorder drives its own headless browser and never shows a terminal or `.env`.

**3. A sample CSV for the upload scene.**

```bat
python -c "from pathlib import Path; from tests.unit.scoring_fixtures import make_shipments_csv; make_shipments_csv(Path(r'C:\temp\prov-video\northwind_shipments.csv'), country=False, cancelled_share=0.03)"
```

**4. Record and edit.**

```bat
cd scripts\video
set VIDEO_SAMPLE_CSV=C:\temp\prov-video\northwind_shipments.csv
node record.mjs
set FFMPEG=ffmpeg
node edit.mjs
```

`node record.mjs flags memory` re-records only those scenes; `node edit.mjs demo`, `reel` or `sheets` builds one part. Recording takes about 15 minutes: headless Chromium is slow to paint the console's glass effects, so the scenes are recorded in real time and the edit trims them. Contact sheets (a frame every 5 s) land in `build/`.

Settings: `CONSOLE_URL` (default `http://localhost:5173`), `VIDEO_REVIEWER` (the approver's name, default `Ajanya`), `VIDEO_BUILD` (default `docs/video/build`).

## Scenes

The demo is a title card, six recorded scenes and a closing card. Each scene is one browser context and one `.webm` in `build/raw/`, with a `.json` of caption cues and marks next to it.

| # | Demo section | Scene in `record.mjs` | Mode | What it shows |
|---|---|---|---|---|
| 1 | Title card (4 s) | `templates.title` in `cards.mjs` | | "Provenance: explainable supply chain risk intelligence" |
| 2 | Overview | `overview` | real | 20 flags across 7 quarters, the Agent card, recent activity from the audit log |
| 3 | Flags | `flags` | real | Golf Balls 2017-Q1: score, top factor, the three weighted factors, supplier history (SCD2); then the Low confidence filter and Boxing & MMA 2017-Q3 |
| 4 | Live agent | `agent` | real (mock fallback) | Cardio Equipment 2017-Q2: **Investigate with agent**, the wait cut to about 3 s and captioned "sped up", the **GROUNDED** badge, hovering the number chips, the proposed action |
| 5 | Approvals | `approvals` | real | Approving that proposal by name, then the **Decided** tab |
| 6 | Memory and pipeline | `memory` | real | Investigations and a report with its human decision; quality gate history with the quarantined batch; **Audit chain: Intact** and **Verify again** |
| 7 | Datasets and pipelines | `datasets` | mock (`?mock=1`), labelled "sample data" | The four steps, uploading a CSV, the gate, **Propose a mapping**, and a scored dataset's results with country risk left out |
| 8 | Closing card (6 s) | `templates.closing` | | 297 tests, top factor 16/16, 94.4% grounded, 0 unapproved actions, 211/211 scores equal |

The live run uses **Cardio Equipment 2017-Q2** rather than Women's Apparel 2017-Q2. Women's Apparel already has a grounded report in the warehouse, and the console shows that report instead of the button, so there is nothing to click. Cardio Equipment is the same quarter and has not been investigated.

If the agent call fails or takes over 40 s, `record.mjs` records that scene again in mock mode, and its caption says so.

Figures on the closing card come from the [README](../../README.md#key-results) results table ([agent_evaluation.md](../agent_evaluation.md), [testing.md](../testing.md)).

### Reel

The reel reuses the demo's scenes without their captions. Each clip is cropped around the card in use, using the box `record.mjs` stores in the scene's `.json` (the `reel_*_box` marks), and fills the full width of the 9:16 frame between a caption band and a brand line.

| Clip | From | Caption |
|---|---|---|
| Hook | `flags`, the Golf Balls sheet | "Most risk scores can't explain themselves. Mine does." |
| Factors | `flags`, score breakdown | "Every flag shows the factors behind its score" |
| Agent | `agent`, the click | "An AI agent investigates, using read-only tools" |
| Grounded | `agent`, badge and chips | "GROUNDED: every number traces to a tool result" |
| Approval | `approvals`, the dialog | "Nothing happens until a named human approves" |
| Quarantine | `memory`, quality gate | "Bad batches are quarantined, not scored" |
| Audit | `memory`, audit chain | "And every decision is in a hash-chained audit log" |
| End card (3 s) | `templates.reelEnd` | Project name, "PG Diploma in Data Science, University of Calicut", "Built with DuckDB, Dagster, React and an AI agent with human approval" |

There is no music: the repository has no royalty-free audio file.

## Files

| File | Role |
|---|---|
| [scripts/video/lib.mjs](../../scripts/video/lib.mjs) | Browser setup (1920x1080, scale 1), a visible cursor overlay, paced glide, click, type and scroll helpers, cue and mark logging |
| [scripts/video/record.mjs](../../scripts/video/record.mjs) | The six scenes |
| [scripts/video/cards.mjs](../../scripts/video/cards.mjs) | Title, closing, caption and reel cards, rendered from HTML with the console's Inter font |
| [scripts/video/edit.mjs](../../scripts/video/edit.mjs) | Trims scenes, speeds up the agent wait, burns in captions, builds both videos and the contact sheets |
| [scripts/video/explore.mjs](../../scripts/video/explore.mjs) | Screenshots of every page, to check layout before recording |
