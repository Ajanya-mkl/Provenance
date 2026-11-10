# User guide

How to use the Provenance console. To start it, see the [README](../README.md#api-and-console); to try it with no setup, use [mock mode](#mock-mode).

Images below are placeholders until the screenshots listed at the [end](#screenshots-to-take) are taken.

The console has no login and serves this machine only. The name you type on a decision is recorded as typed.

## Overview

![Overview](images/overview.png)

The first screen shows:

- **KPI tiles:** flags in the latest flagged quarter, high-tier flags, pending approvals, and the grounded rate from the latest evaluation.
- **New dataset:** the primary button, top right.
- **Agent card:** investigations done, grounded rate, proposals awaiting approval, the last agent action and the model, with links to Investigations and Approvals. It covers all data, whichever dataset is selected.
- **Flags by quarter:** a chart with a data table behind "Show data table".
- **Recent activity:** the latest audit entries. The agent's own entries have a bot icon and the label "Agent".

## Reading flags

![Flags](images/flags.png)

**Flags** lists supplier-quarters whose risk tier rose and held. A "supplier" here is a product category. Each row shows the tier change, score, top factor, confidence, and whether it has been investigated. Filter by tier and confidence, or search.

Select a row to open the detail in a side panel; **Open as page** gives it its own URL.

![Flag detail](images/flag-detail.png)

- **Score ring:** the score out of 1, with the tier in words.
- **The sentence:** which factor drove the flag, what it contributes, and which way it moved.
- **Confidence:** "Low confidence" means fewer than 3 eligible quarters so far, under 95% country coverage, or (for a dataset) a factor left out.
- **Score breakdown:** the weighted factors as one bar. Hover or focus a segment for its numbers.
- **Destination markets:** share of shipments, DataCo only.
- **Supplier history:** the SCD2 versions of its delivery band. For a dataset, this is its tier per scored quarter.

## Running an investigation

![Investigate with agent](images/investigate.png)

The **Agent investigation** panel on a flag shows the report if there is a grounded one, and otherwise the **Investigate with agent** button.

1. Click it. The status shows the agent reading the flag and calling its tools, then checking numbers and recording. In real mode this took about 14 s in one run.
2. A **GROUNDED** badge means every number traces to a tool result. The summary and proposed action appear.
3. A red **NOT GROUNDED** banner names the numbers no tool returned. No action is created, and you can investigate again.

**The agent only proposes.** Nothing takes effect until a named person approves it.

Refusals explain themselves: "The warehouse is busy" means another writer is running, so try again when it finishes. A missing key says so, and names `LLM_API_KEY`.

**Investigations** lists every report. Open one for the full view:

![Investigation](images/investigation.png)

- Focus or hover a number in the summary to see which tool it came from; the matching tool lights up.
- The right side shows the tools used, past cases recalled, tokens and the proposed action.

## Approving or rejecting

![Approvals](images/approvals.png)

**Approvals** lists what the agent proposed; the sidebar shows the pending count. Filter by Pending, Decided or All.

- **Approve** needs your name, and a reason is optional. The action is approved and applied at once: a watchlist entry or a review request. `no_action` records the decision and changes nothing.
- **Reject** needs your name and a reason.
- Each item shows its own audit trail.

Decisions are refused with a clear message when the warehouse is busy; nothing is half-recorded.

## The audit trail

![Pipeline](images/pipeline.png)

**Pipeline** shows quality gate results per source and every batch checked, the **Audit chain** status (intact or broken, rows verified, when it was checked), and the audit log, newest first. The chain is recomputed on each load, so an edited or deleted row shows as broken.

## Uploading a CSV

![Datasets](images/datasets.png)

**Datasets & pipelines** lists your datasets with a status chip, row and column counts and the upload date, under a four-step explainer: Upload → Map → Run → Results. A link on each row goes to its next step.

| Status | Meaning |
|---|---|
| Gated | Passed the quality gate; next, review the mapping |
| Quarantined | A hard rule failed; it was not loaded |
| Mapped | A mapping is approved; next, run scoring |
| Scored | Results are ready |

**Upload dataset** opens the upload screen.

![Upload](images/upload.png)

1. Drop a `.csv`, or choose one, up to 50 MB. Other file types and oversize files are refused in the browser.
2. Give it a name and click **Upload and check**. Progress shows, then profiling and the gate run.
3. Read the result:
   - **Passed the quality gate:** rows loaded, and any warnings. Continue to **Review mapping**.
   - **Quarantined by the quality gate:** where the file went and why, with each failed rule, why it exists, and whether it is hard or tolerance.
   - **Not onboarded:** the file failed a pre-check, and nothing was stored.
   - **Already registered:** the same bytes were uploaded before, with a link to that dataset.

## Reading the profile and generated rules

![Profile](images/profile.png)

**Profile** shows each column's inferred type, percent empty, distinct values, range and detected date format, with notes for candidate keys, mixed types and mostly-empty columns.

**Generated quality rules** were built from this file's own profile. Each states why it exists, and has a badge:

- **Hard:** a failure quarantines the file.
- **Tolerance:** a failure is only a warning.

Because rules come from the same file, passing means the file is structurally consistent, not that its values are right.

## Reviewing and editing a mapping

![Mapping review](images/mapping.png)

**Mapping** has the profile on the left and the proposal on the right. If there is no proposal yet, enter your name and click **Propose a mapping**.

The proposal shows **Decided by**: Heuristic, Agent, or Human-edited, with a confidence or "no model confidence", and why the agent was asked, if it was. For each field you see the column, the evidence, and a control:

- **Entity:** the supplier, vendor or category column.
- **Event date:** the column and its format. Prefer the order or creation date.
- **Outcome:** a late flag (with a **polarity** toggle: late when true, or late when false for an on-time flag), scheduled and actual durations, or a delay value; with a **unit** of days or hours.
- **Country:** optional.
- **Cancelled rule:** optional. Rows where the column equals the value count as shipments but not toward lateness.

**Mappable** and **Scoreable** verdicts sit below, each with its reason.

Then enter your name (required), and choose:

- **Save edit:** creates a new version; earlier versions are kept. If the edit is invalid, the validator's message appears and nothing is saved.
- **Approve:** available when there are no unsaved changes and the mapping is mappable. The dataset becomes Mapped.
- **Reject:** needs a reason.

A not-mappable file, such as student marks, cannot be approved; the reason says what is missing.

## Running scoring

![Run](images/run.png)

**Run** → **Start run** starts scoring in the background. The stepper follows four stages: Silver, Metrics, Scoring, Flags.

- **The warehouse is busy:** another writer is running. Try again when it finishes.
- **The run failed; nothing was changed:** with the reason in plain words, such as too few eligible quarters. Scoring is one transaction, so earlier results stay.
- **"The warehouse was busy" or "failed unexpectedly" with nothing else running:** a [known bug](limitations.md#running-it). The console's own polling can collide with a run as it starts. Click **Run again**.
- **See results** appears when it finishes.

Running again rebuilds with the same frozen bounds and creates no duplicate events.

## Reading results and excluded factors

![Results](images/results.png)

- **KPI tiles:** entities, quarters, active flags, rows rejected.
- **Scores by entity:** risk score per quarter, with dashed tier thresholds and a data table. At most five entities are drawn; all are in the table.
- **Factors and weights:** every factor, its base weight and the weight used. An excluded factor shows the reason, such as "no country column". The remaining weights are renormalised and confidence is low.
- **Flags:** links to the flag detail.
- **Rejected rows:** counts by reason.
- **Country match:** the share of rows matched to an ISO3 code, and which values did not match.
- **Rules:** the rules version, how bounds were set, and the bounds.

**Scores are comparable within this dataset only.** A dataset's bounds come from its own percentiles.

## Switching datasets

![Dataset selector](images/selector.png)

**Showing data from** in the sidebar switches Overview and Flags between DataCo and any scored dataset. Your choice is remembered in this browser. On a narrow window, the selector is in the page header instead.

## Mock mode

`npm run dev:mock` serves fixtures generated from the real API, with no API, warehouse or key. A banner in the sidebar says it is mock data. Decisions, uploads and runs work but stay in memory, and a page reload resets them.

| Add to the URL | Shows |
|---|---|
| `?mock=busy` | Busy runs, and "Pipeline running, showing last loaded data" |
| `?mock=locked` | Approve and reject refused because the warehouse is locked |
| `?mock=nokey` | An agent run refused for a missing key |
| `?mock=runfail` | A scoring run that fails |
| `?mock=1` | Back to normal |

A variant stays on for the browser tab until you change it. Uploading a file named like `dirty` shows a quarantine.

## Screenshots to take

Save them to `docs/images/` under these names. Start mock mode with `npm run dev:mock` and use `http://localhost:5173`. In the table, `<cardio>` means `risk_flag%3A1.2%3A2017-04-01%3Acat_cardio_equipment`, and `<hockey>` means `risk_flag%3A1.2%3A2016-01-01%3Acat_hockey`.

| File | URL | What should be visible |
|---|---|---|
| `overview.png` | `/` | KPI tiles, New dataset button, Agent card, chart, and Recent activity with bot icons |
| `flags.png` | `/flags` | The flag list and its filters |
| `flag-detail.png` | `/flags/<hockey>` | Score ring, sentence, grounded report, score breakdown |
| `investigate.png` | `/flags/<cardio>` | The Investigate with agent button; optionally a second shot of the live status after clicking |
| `investigation.png` | `/investigations/<hockey>` | GROUNDED badge, the summary with a number focused, and the lit tool |
| `approvals.png` | `/approvals` | Pending items, with the decision dialog open and a name typed |
| `pipeline.png` | `/pipeline` | Gate history, Audit chain "intact", audit log |
| `datasets.png` | `/datasets` | Four-step explainer, the list with status chips, Upload dataset button |
| `upload.png` | `/datasets/upload` | After uploading a file named `shipments_dirty.csv`: the quarantine explanation and failed rules |
| `profile.png` | `/datasets/14b94184aeb4` | Column table and generated rules with Hard and Tolerance badges |
| `mapping.png` | `/datasets/14b94184aeb4/mapping` | Profile and proposal side by side; change Entity to `unit_price` and Save edit to show the validator's message |
| `run.png` | `/datasets/83384e3e01b2/run` | After Start run: the stepper mid-run |
| `results.png` | `/datasets/83384e3e01b2/results` | KPIs, chart, Factors and weights with country risk excluded, the comparability note |
| `selector.png` | `/` | With "Northwind regional shipments" chosen in the sidebar, showing its flags |

Optional: `/datasets/7717da472e79/mapping` for an agent-decided, not-mappable proposal, and `/datasets/83384e3e01b2/run?mock=busy` for the busy state.
