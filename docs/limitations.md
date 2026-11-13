# Limitations

Known limits, collected in one place. None of them is hidden in the product, but they change what you can claim from it.

## The data

- **DataCo is likely synthetic.** It is a public benchmark attributed to a company, and at least one paper describes it as synthetic. Treat results as a demonstration, not a finding about real suppliers.
- **A supplier is a product category.** DataCo has no supplier identifier, so `supplier_key` is a category. Scores describe categories, and the console says so.
- **The October 2017 catalogue change.** Of 32 categories with shipments before 2017-Q4, 19 have none in Q4, and 18 of Q4's 31 categories are new. It looks like a catalogue replacement, not continuous supplier history. New categories have one eligible quarter and stay low confidence ([data_profile.md](data_profile.md#october-2017-catalogue-change)).
- **USASpending is not used for scoring.** It is ingested, gated and modelled, but its vendors do not join to DataCo, and no synthetic bridge was built.
- **Country risk is destination-based.** It reflects where shipments go, not where a supplier operates.

## History and scores

- **SCD2 reflects the data as currently loaded.** The dimension is rebuilt from the series on every run, so changing a threshold or loading new data rewrites past versions. It is reproducible, but it is not a record of what was known at the time.
- **Scores are comparable within a dataset only.** Each dataset has its own frozen bounds (5th to 95th percentile), so 0.7 in one dataset and 0.7 in another mean different things, and a dataset's scores are relative to itself.
- **Frozen bounds can go stale.** They stay fixed until `--refreeze`, so later data can sit clipped at a bound.
- **Flags of an older rules version are deleted**, not resolved. Their events are kept and marked superseded. Flags of the current version are resolved and never deleted.
- **A flagship `rules_version` change currently fails when a flag recurs.** Reproduced on a warehouse copy: with the version changed to 9.9, `score_supplier_risk` raised a DuckDB `ConstraintException` (duplicate key `cat_accessories`, 2016-10-01). The old version's flag rows are deleted and the same key inserted in one transaction, which DuckDB's index check refuses. The transaction rolls back, so the 20 flags stay intact: it fails safely, but it blocks the change until fixed. Dataset flag tables avoid this by having no primary key.
- **Partial quarters are judged across the whole dataset**, not per entity, as in the flagship.
- **Entity keys can collide.** Names are lower-cased with other characters replaced by `_`, cut to 64 characters, so two distinct names can merge. The flagship does this on purpose (one category name has two ids); an uploaded file could do it by accident.

## The agents

- **Evaluation sets are small and partly synthetic.** The investigation agent has 18 cases at one run each, with ground truth taken from the pipeline itself, so it measures faithfulness, not correctness. The mapping evaluation has 11 small synthetic fixtures.
- **Mapping fixes were made after seeing the cases.** Finding a country by its values, `YYYYMMDD` dates, the `ord`/`shp` abbreviations and the order-date preference were added after the hard cases were written, so those gains are not independent evidence.
- **Results are cached.** A cached case reflects the model when it was first run. Report dates are in the reports; `--fresh` measures again.
- **Grounding is numeric.** It proves each number came from a tool, not that the sentence around it reads that number correctly.
- **The mapper's catch-all fallback can hide agent-loop bugs.** Every exception in the mapping agent's loop becomes a heuristic proposal, so a real bug would appear only as "invalid response (TypeError)" in the note, not as a crash. That is deliberate: proposing a mapping must not fail because of the model.
- **Model output is not reproducible.** Temperature 0 does not guarantee the same answer.
- **Prompts leave the machine.** Groq receives aggregates and category names, and for mapping the column names. Text values are not sent unless `--sample-values` is used.

## Agent workflow gaps

- **The sensor does not investigate.** `agent_investigation_sensor` (stopped by default) moves events to `queued` and starts a job that only logs them. Investigations start from the CLI or the console.
- **`investigated` is never set.** `agent.events.investigation_status` allows it, but nothing writes it. The API works out whether a flag has been investigated from `agent.investigations`, so nothing depends on the column.
- **Effects are records only.** An applied action adds a row to `agent.watchlist` or `agent.review_requests`. Nothing reads those rows automatically or sends an alert.
- **Only risk flags can be investigated.** `dq_failure` events are recorded and shown, but the agent's tools and the run endpoint accept only `risk_flag` events.

## Running it

- **Localhost only, no login.** The API binds to 127.0.0.1 and refuses other addresses. Anyone on the machine can act, and the `by` name on a decision is typed, not authenticated. Never expose it to a network.
- **The audit chain detects tampering; it does not prevent it.** Someone with the file could rewrite the whole chain.
- **Single-writer DuckDB.** A reader cannot open while a writer holds the file, and the reverse. A scoring run blocks all reads for its duration, a running investigation counts as a writer until it finishes, and a second run gets 409 `busy`.
- **The first pipeline run fails at silver.** The gold marts' schema tests are declared in `models/silver/_silver.yml`, and a singular test reads gold too, so the silver step's path-based selection runs them before gold exists. On an empty warehouse, `silver_dbt_assets` fails with `Table with name mart_shipment_delay does not exist`, and the restore point rolls silver back. The workaround, in the README, is one whole-project `dbt build`, after which the pipeline runs normally (checked on a fresh clone). The fix is to move those tests to a YAML file under `models/gold`. Existing warehouses are not affected.
- **Polling can make a scoring run fail, often.** This is the most important open bug. While a run starts, the console polls `GET /api/runs/{id}` every second. That poll opens a read connection to the warehouse, and if it lands as the subprocess opens its writer, DuckDB refuses the writer, which has no retry. On Windows the poll's read of the status file can also make the subprocess's `os.replace` of that file fail. Measured on a warehouse copy, polling at 1 s: **4 of 12 runs failed**, 3 with "The warehouse was busy" and 1 with "Scoring failed unexpectedly (PermissionError)". It fails safely: nothing is changed, and running again usually works. The fix is for the endpoint to read only the status file until a run finishes, to retry a locked writer open at the start of a run, and to retry the status file's replace. An agent investigation's poll reads only the status file, so it can meet the second collision but not the first.
- **Upload onboarding runs inside the API process**, in a worker thread. Only scoring and investigations run as subprocesses, so a large upload ties up the request and holds the writer while it runs.
- **Windows is the tested platform.** Development, the fresh-clone check and all local runs are on Windows with Python 3.12. CI is configured for Ubuntu and has not been run yet (to verify).

## Dataset onboarding

- **One batch per dataset.** A dataset is one file, identified by its hash. A new version of the data is a new dataset with its own bounds, so there is no incremental loading and no history across files.
- **No composite keys.** Profiling looks for a single-column identifier. A file whose rows are identified by two columns gets no uniqueness rule.
- **Padded ranges.** The numeric tolerance rule allows the profiled range padded by 10%. Since rules are generated from the same file they then check, a clean file passes by construction. The gate catches structural problems, not whether the values are right.
- **One bad date quarantines a dataset.** "Dates parse with the detected format" is a hard rule even when 99.9% parse.
- **The mappers read names, not meaning.** With no text values, neither can tell a code from a name by content, and neither can tell days from hours by value alone.
- **Scoreable is an estimate at mapping time.** It counts rows per quarter; the flagship's stricter eligibility is applied when scoring, which can still refuse with a reason.

## Tests

- **The subprocess `.env` gap.** `tests/conftest.py` disables `load_dotenv` in the test process and sets a dummy key and an unreachable address, which subprocesses inherit. A subprocess runs the real `load_dotenv`, which does not override a set variable. But a future test that deleted or blanked the key and then started a subprocess would load the real key (on Windows, blanked variables do not reach child processes). No current test does this.
- **The equivalence test needs local data** and is deselected by default, so CI does not run it.
- **dbt models are tested by dbt**, at pipeline time, not in `pytest -q`.
- **No browser tests.** Web tests run in jsdom, so layout and visual design are not checked automatically.

## Future work

1. Stop polling from disturbing scoring runs (see [Running it](#running-it)): the most urgent item.
1. Move the gold schema tests so the first pipeline run succeeds without a manual `dbt build`.
1. Fix the `rules_version` delete-then-insert failure so the flagship's rules can change.
2. Let the sensor start real investigations, and set `investigated`.
3. Multi-batch datasets: new rows for the same dataset, with bounds drift reported.
4. Investigate `dq_failure` events with the agent.
5. Authentication, and real identities on decisions, before any shared use.
6. Effects that do something: a notification or a ticket for an applied action.
7. A larger, independent evaluation set for both agents, with repeated runs.
8. Composite keys in profiling, and tighter generated rules.
9. Real supplier identifiers, if a dataset with them becomes available.
10. Browser end-to-end tests for the console.
