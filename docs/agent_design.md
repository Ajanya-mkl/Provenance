# Agent design

Provenance has two agents. Both use the same LLM client and both are bounded by deterministic code: the model can read and propose, and code decides what counts.

**Provider and model.** Groq through `agent/llm.py`, with the model from `LLM_MODEL_NAME` (`openai/gpt-oss-120b` in `.env.example`, and the one every evaluation used; the code's default if unset is `llama-3.3-70b-versatile`). Temperature 0, at most 1,024 tokens per reply. The client retries a 429 and Groq's malformed tool-call error up to 3 attempts, honouring `retry-after`, with backoff from 2 s capped at 60 s. Temperature 0 does not make it deterministic, so evaluations cache answers.

## Investigation agent

Explains one risk flag and proposes what to do. It never changes a score.

### Tools

`agent/tools/investigation_tools.py`. All read-only: fixed, parameterised SQL on a read-only connection opened per call and closed at once, so nothing holds the warehouse while the model thinks. The model supplies arguments only; bad arguments return `{"error": ...}` instead of raising, and results are small, rounded aggregates.

| Tool | Returns |
|---|---|
| `get_flag(event_id)` | The flag: supplier, quarter, tier change, score, confidence, top factor |
| `get_supplier_history(supplier_key)` | SCD2 versions (for a dataset, tier per scored quarter) |
| `get_score_breakdown(supplier_key, quarter)` | Score, every weighted factor, previous quarter, quarter metrics, excluded factors |
| `get_country_risk(supplier_key, quarter)` | Destination risk, trailing value, coverage, top destinations |
| `get_market_mix(supplier_key, quarter)` | Market shares against the previous quarter (DataCo only; the contract has no market column) |
| `recall_past_investigations(supplier_key, limit)` | Earlier grounded cases of this supplier and the human decision |

Given a `dataset_id`, which comes from the `ds_<id>:` event prefix, the tools read that dataset's tables. A toolbox refuses another dataset's event, and recall stays within one dataset.

### The loop

`agent/loop.py`, at most 6 model calls.

1. **Prelude.** The loop itself calls `get_flag` and `recall_past_investigations`, so memory is used on every run rather than when the model remembers to.
2. **Steps.** The model calls tools or answers. The last step forbids tools and asks for the report.
3. **Report.** JSON validated with pydantic: `summary`, `top_factor`, `evidence` (at least one, each naming a tool), `proposed_action`, `confidence`. An invalid report gets the problem back as a message.

The system prompt requires every number to be copied from a tool result, forbids computing new ones or speculating about causes, and requires "insufficient data" and `no_action` for a low-confidence flag or a tool error. It sets the expected action: `request_review` for a new high tier with full confidence, `watchlist` for a new medium tier with full confidence, otherwise `no_action`.

### Grounding check

`agent/grounding.py`, no LLM involved. Every number in the report is extracted (quarters and dates are handled as labels) and must match a number in that run's tool results, within half a unit of its last decimal place plus 1e-6. A percentage also matches its fraction, so 63.1% is grounded by 0.631. Evidence must cite a tool that returned a successful result.

The outcome becomes the status: `proposed` (grounded), `rejected_ungrounded` (no action is created, and the console shows a red NOT GROUNDED banner naming the numbers) or `no_report` (out of steps). The check proves each number came from a tool. **It does not prove the sentence around it reads that number correctly.**

### Recall memory

`agent/memory/investigation_memory.py`. Up to 5 earlier grounded investigations per supplier: quarter, tier change, score, top factor, proposed action, and the human decision and reason (cut to 200 characters), which approvals write back. Only allow-listed factor and action values pass through.

### Approval guardrail, in code

`agent/approval.py`. The model can only propose:

- `sanitize_action` allows only `no_action`, `watchlist` and `request_review`. Anything else is stored as `no_action` and audited as `action_type_rejected`.
- The supplier and quarter on an action come from the pipeline's event, never from the model.
- `apply_action` is the **only** place an action takes effect. It requires status `approved`, a `decided_by` name **and** the matching approval in the audit log. Anything else writes `apply_refused`.
- `AGENT_REQUIRE_APPROVAL=false` does not bypass this; it only logs a warning. A test covers both settings.
- Effects are rows in `agent.watchlist` or `agent.review_requests`. Nothing sends an alert or changes a score.

### Hash-chained audit log

`agent/store.py`. Each row stores the previous row's hash and a SHA-256 over itself and that hash, so an edited or deleted row breaks the chain. The module only ever INSERTs. `verify_audit_log` recomputes the chain; the Pipeline screen and `approve --audit` report it. The chain detects tampering; it does not prevent it, because anyone with the file could rewrite the whole chain.

### Run paths

- **CLI:** `python -m provenance.agent.run --next | --event-id <id>`. It refuses to start (exit 3) while Dagster has an active writer run or the file is locked.
- **Console:** the "Investigate with agent" button calls `POST /api/investigations/{event_id}/run`, which starts `provenance.agent.background` as a subprocess, never in the API process. It is the same investigation. It answers 400 if `LLM_API_KEY` is missing and 409 `busy` if a writer is active. Status goes to `data/datasets/runs/<run_id>.json`, and `GET /api/runs/{run_id}` reports `investigating`, then `recording`.
- **Sensor:** only queues events and logs them (see [limitations](limitations.md)).

### Failure handling

| Failure | What happens |
|---|---|
| No key | CLI: `LLMError`. Console: 400 with a clear message |
| Rate limit | Retried; then `LLMError`, reported plainly |
| Connection, timeout, service error | Run marked failed, naming only the error type; nothing recorded |
| Wrong key | "The model service refused LLM_API_KEY" |
| Warehouse locked | Tool returns "busy" to the model; a blocked write fails the run with a plain message, nothing recorded |
| Process dies | `GET /api/runs` reports failed: "stopped unexpectedly" |
| Out of steps | Status `no_report`, no action |

### Privacy

The model sees only supplier-category aggregates from fixed queries. Customer personal data stays in bronze and no tool reads bronze. Groq receives the prompts, so the aggregates and category names leave the machine.

### Evaluation

`python -m provenance.agent.eval.eval_runner` runs on a temporary copy of the warehouse and caches results. Report: [agent_evaluation.md](agent_evaluation.md).

| Metric | Result | n |
|---|---|---|
| Top factor matches the pipeline | 1.0 | 16 |
| Grounded | 0.944 | 18 |
| Valid action | 1.0 | 18 |
| Low-confidence handled | 1.0 | 2 |
| Edge case handled | 1.0 | 2 |
| Memory recalled | 1.0 | 1 |
| Guardrail violations | 0 | 18 |
| Average steps, tokens, latency | 2.5, 4,244, 9.23 s | 18 |

The one ungrounded case is `edge_unknown_quarter`, whose evidence cited a tool that had returned an error.

**Limits.** The expected factor is the scorer's own, so this measures whether the agent reports the pipeline faithfully, not whether the pipeline is right. Eighteen cases at one run each move the rates by 5–7 points per case. Only one current flag is low-confidence; the second, and the edge cases, are synthetic. 17 of the 18 cases in the report came from cache.

## Mapping agent

Works out how an unfamiliar CSV fits the shipment contract, only when the heuristic is unsure. `datasets/mapping_agent.py`.

### The contract

`datasets/contract.py`.

| Field | Required | Shape |
|---|---|---|
| `entity` | yes | Column naming who is scored: supplier, vendor or category |
| `event_date` | yes | `{col, format}`: the order or creation date, preferred over ship or delivery dates |
| `outcome` | yes | One of the kinds below |
| `country` | no | Destination or origin column |
| `cancelled` | no | `{col, value}`: matching rows count as shipments but not toward lateness or delay |

| Outcome kind | Fields | Late when |
|---|---|---|
| `late_flag` | `col`, `late_when` | The flag equals `late_when`, so `on_time` maps with `late_when: false` (the polarity) |
| `days_pair` | `scheduled_col`, `actual_col`, `unit` | Actual > scheduled |
| `delay_value` | `col`, `unit` | Delay > 0 |

`unit` is `days` or `hours`. A mapping also carries `evidence`, `confidence` and `notes`. Two verdicts follow, each with a plain reason: **mappable** and **scoreable**.

### Heuristic first

`propose_mapping` runs `heuristic_mapper.py` first: deterministic matching on names (English, Spanish, German, French) and profiled types, plus finding a country column by its values (at least 80% of rows are countries, reported as `country_match_pct`). The agent is asked only when:

- a required field is unmapped,
- the heuristic's mapping is invalid,
- two candidates rank equally, or
- confidence is below 0.8 (`MAPPING_AGENT_CONFIDENCE_THRESHOLD`).

`decided_by` records the path (`heuristic`, `agent`, `heuristic_fallback`, or `human` for an edit), and the console shows it with the escalation reasons.

### Tools and privacy

Read-only tools over the in-memory profile: `get_contract`, `get_dataset_profile`, `get_column_profile`. The model never touches the warehouse, and never writes SQL or code; it returns JSON.

**By default it sees no text values.** It gets column names, types, null percentages, distinct counts, numeric and date ranges, and for text columns only length statistics. A test checks that values of text columns appear nowhere in what is sent. `--sample-values` on the CLI is an explicit opt-in. Column names themselves are sent to Groq. The heuristic's country check reads values, but only locally to count them.

### Deterministic validator

`datasets/validator.py` checks each answer:

- every column exists and is used once;
- types fit roles (an entity is text or a whole number; durations and delays are numeric; a flag is boolean-like);
- the date format parses at least 95% of the column;
- polarity agrees with the column's name and, when one exists, with a numeric delay in the data;
- `late_when` given as a raw value (`no`, `N`, `0`) is normalised only if the column really contains it;
- a `cancelled` value must occur in its column;
- every number in `evidence` appears in the profile or that run's tool results, using the same grounding check.

A required field set to null is a valid answer meaning "no column fits": it is not mappable, with the reason.

### Retry and fallback

A rejected answer gets one retry with the validator's messages, or a request to fix a wrong-shaped value. After two rejections, the heuristic takes over with `source='heuristic'`.

**Any other model failure falls back too:** connection errors, timeouts, rate limits, service errors, invalid responses, a client that cannot be built. The stored note gives the category and error type, never the provider's message. Proposing a mapping therefore never fails because of the model. The catch-all can hide a real bug in the loop; [limitations](limitations.md) records that.

### Human edit and approval

`datasets/mapping_store.py`, writing through `registry.writer`.

- Every proposal or edit is a new version in `meta.mappings`. Nothing is overwritten; an edit supersedes the previous version.
- An edit is re-validated before it is written, and an invalid edit returns the validator's message.
- A human-edited version has no confidence and is shown as human-edited. Evidence for an edited field is dropped.
- Only approving a mappable mapping, with a reviewer name, moves the dataset to `mapped`. A rejection requires a reason.
- Propose, edit, approve and reject each append to the same audit log.

### Evaluation

`python -m provenance.datasets.eval.run` onboards each fixture into a temporary database. Report: [mapping_evaluation.md](mapping_evaluation.md).

| Field | Heuristic only | Agent only | Heuristic-first |
|---|---|---|---|
| entity | 10/11 | 10/11 | 10/11 |
| event_date | 9/10 | 10/10 | 10/10 |
| outcome | 10/11 | 11/11 | 11/11 |
| country | 10/10 | 10/10 | 10/10 |
| mappable | 10/11 | 11/11 | 11/11 |
| **Every field right** | 9/11 | 10/11 | 10/11 |
| Agent calls, tokens | 0, 0 | 11, 66,988 | 3, 19,368 |

The remaining agent miss names `subject` as the entity of the student-marks file, though it still marks the file not mappable.

**Limits.** Eleven small synthetic cases are a smoke test, not a benchmark. Cases a–g were written alongside the heuristic's word lists. Cases h–k were written to be hard, but the country-by-values check, `YYYYMMDD` dates, the `ord`/`shp` abbreviations and the order-date preference were added **after** seeing them, so those gains are not independent evidence. Heuristic-first reuses the agent-only run for the cases it escalates. All fixtures are too small to score, so the scoreable verdict is unit-tested with synthetic counts instead.
