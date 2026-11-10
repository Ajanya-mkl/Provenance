# Data dictionary

Every schema and table in the warehouse (`data/provenance.duckdb`). Column names and types were read from the live schema of a **copy** of the warehouse with one synthetic dataset onboarded and scored in it; the meanings are written by hand, and the generator refused to finish while any live column lacked one. Scoring runs and investigations add rows, not columns.

## Contents

- [`bronze`](#schema-bronze): `dataco_shipments`, `dq_results`, `usaspending_contracts`, `worldbank_lpi`
- [`silver`](#schema-silver): `country_iso3_map`, `dim_supplier_scd2`, `fact_purchase_orders`, `fact_shipments`, `int_supplier_quarter_metrics`, `stg_dataco_order_items`, `stg_usaspending_awards`, `stg_worldbank_lpi`, `wb_country_reference`
- [`gold`](#schema-gold): `mart_shipment_delay`, `mart_supplier_risk`, `supplier_risk_flags`, `supplier_risk_score`
- [`agent`](#schema-agent): `actions`, `audit_log`, `events`, `investigations`, `review_requests`, `watchlist`
- [`meta`](#schema-meta): `dataset_columns`, `dataset_rules`, `datasets`, `mappings`, `runs`
- [`ds_<id>`](#schema-ds_id): one schema per onboarded dataset: `bronze_raw`, `silver_rejects`, `silver_shipments`, `country_map`, `quarter_metrics`, `mart_risk`, `risk_score`, `risk_flags`
- [Lineage columns](#lineage-columns)
- [Statuses](#statuses)
- [Files outside the warehouse](#files-outside-the-warehouse)

## Lineage columns

Every bronze table, DataCo, USASpending, World Bank and each `ds_<id>.bronze_raw`, carries:

| Column | Meaning |
|---|---|
| `_batch_id` | Batch id: the SHA-256 of the source file or payload. Lineage. |
| `_source` | Source name (for a dataset, `dataset:<id>`). Lineage. |
| `_source_file` | File the row came from. Lineage. |
| `_loaded_at` | When the batch was loaded. Lineage. |

Staging renames `_batch_id` and `_loaded_at` to `batch_id` and `loaded_at`; `ds_<id>.silver_shipments` keeps `_batch_id`.

## Statuses

| Where | Values | Meaning |
|---|---|---|
| `bronze.dq_results.status` | `validated`, `passed_with_warnings`, `quarantined` | Gate verdict; only the first two reach silver. |
| `agent.events.investigation_status` | `open`, `queued`, `investigated` | `open` at creation. The sensor moves events to `queued`. Nothing currently sets `investigated`: see [limitations](limitations.md). |
| `agent.events.resolved_at` / `resolution` | null, or set with a reason | Set means superseded (`superseded: payload changed; replaced by <id>`) or no longer qualifying (`resolved: no longer qualifies under rules <v>`). A set value is what "resolved" means; the API reports such events as `resolved`. |
| `gold.supplier_risk_flags.status`, `ds_<id>.risk_flags.status` | `active`, `resolved` | A flag that stops qualifying is resolved, not deleted. |
| `agent.investigations.status` | `proposed`, `rejected_ungrounded`, `no_report` | Grounded report, report with an unsourced number, or no report within the step limit. |
| `agent.actions.status` | `proposed` → `approved` → `applied`, or `rejected` | Only a named human moves it past `proposed`. |
| `meta.datasets.status` | `uploaded` → `profiled` → `gated` or `quarantined` (or `failed`); `gated` → `mapped` | Only an approved mapping gives `mapped`. |
| `meta.mappings.status` | `proposed`, `approved`, `rejected`, `superseded` | Versions are never overwritten. |
| `meta.runs.status` | `queued`, `running`, `succeeded`, `failed` | `succeeded` is written in the same transaction as the scores. |

## Schema `bronze`

Raw source data plus lineage, one Parquet folder per batch under `data/bronze/<source>/batch_id=<hash>/`, exposed as tables. Batch ids are content hashes. A batch that fails the gate is moved to `data/quarantine/`.

### `bronze.dataco_shipments`

DataCo order items exactly as in the CSV, plus lineage. Every source column is kept, including customer personal data, which stops here: staging never selects it.

- **Type:** table
- **Grain:** One row per order item (`Order Item Id`).
- **Producer:** `dataco_shipments` asset (`ingestion/sources/dataco_shipments.py`).
- **Consumers:** `gate_dataco_shipments`; `silver.stg_dataco_order_items` (gate-approved batches only).

The 53 source columns, named as in the CSV:

| Group | Columns |
|---|---|
| Delivery | `Type`, `Days for shipping (real)`, `Days for shipment (scheduled)`, `Delivery Status`, `Late_delivery_risk`, `Shipping Mode`, `shipping date (DateOrders)`, `order date (DateOrders)` |
| Money | `Benefit per order`, `Sales per customer`, `Sales`, `Order Item Discount`, `Order Item Discount Rate`, `Order Item Product Price`, `Order Item Profit Ratio`, `Order Item Quantity`, `Order Item Total`, `Order Profit Per Order`, `Product Price` |
| Product and category | `Category Id`, `Category Name`, `Department Id`, `Department Name`, `Order Item Cardprod Id`, `Product Card Id`, `Product Category Id`, `Product Description`, `Product Image`, `Product Name`, `Product Status` |
| Order and destination | `Order Id`, `Order Item Id`, `Order Customer Id`, `Market`, `Order City`, `Order Country`, `Order Region`, `Order State`, `Order Status`, `Order Zipcode` |
| Customer (personal data; never reaches silver) | `Customer City`, `Customer Country`, `Customer Email`, `Customer Fname`, `Customer Id`, `Customer Lname`, `Customer Password`, `Customer Segment`, `Customer State`, `Customer Street`, `Customer Zipcode`, `Latitude`, `Longitude` |

Lineage:

| Column | Type | Meaning |
|---|---|---|
| `_batch_id` | VARCHAR | Batch id: the SHA-256 of the source file or payload. Lineage. |
| `_source` | VARCHAR | Source name (for a dataset, `dataset:<id>`). Lineage. |
| `_source_file` | VARCHAR | File the row came from. Lineage. |
| `_loaded_at` | TIMESTAMPTZ | When the batch was loaded. Lineage. |

`Order Country` holds Spanish names; the seed `silver.country_iso3_map` translates them. `Late_delivery_risk` equals real > scheduled days on every row that was not cancelled (checked on the warehouse copy).

### `bronze.dq_results`

One gate verdict per batch. Staging reads only batches whose status is `validated` or `passed_with_warnings`.

- **Type:** table; primary key (source, batch_id)
- **Grain:** One row per (`source`, `batch_id`).
- **Producer:** `data_quality/gate.py`.
- **Consumers:** Staging models; `/api/quality`; the Pipeline screen.

| Column | Type | Meaning |
|---|---|---|
| `batch_id` | VARCHAR | The batch checked. |
| `source` | VARCHAR | `dataco_shipments`, `usaspending_contracts` or `worldbank_lpi`. |
| `status` | VARCHAR | `validated`, `passed_with_warnings` or `quarantined`. |
| `passed_expectations` | INTEGER | Rules passed. |
| `failed_expectations` | INTEGER | Rules failed. |
| `checked_at` | TIMESTAMPTZ | When the gate ran. |
| `result` | JSON | Full Great Expectations result (never returned by the API). |
| `warnings` | JSON | Tolerated problems, such as date-order rows. |

### `bronze.usaspending_contracts`

Federal contract awards from the USASpending API, last complete month per run. Loaded and modelled, but no silver fact, gold mart or score reads it: it does not join to DataCo (see `docs/data_profile.md`).

- **Type:** table
- **Grain:** One row per award per batch; overlapping batches accumulate.
- **Producer:** `usaspending_contracts` asset (`ingestion/sources/usaspending_contracts.py`).
- **Consumers:** `gate_usaspending_contracts`; `silver.stg_usaspending_awards`.

| Column | Type | Meaning |
|---|---|---|
| `internal_id` | BIGINT | Award's internal id in the API. |
| `Award ID` | VARCHAR | Public award id. |
| `Recipient Name` | VARCHAR | Vendor name. |
| `Recipient UEI` | VARCHAR | Vendor's Unique Entity Identifier. |
| `recipient_id` | VARCHAR | API recipient id. |
| `Start Date` | VARCHAR | Award start, as text. |
| `End Date` | VARCHAR | Award end, as text. |
| `Award Amount` | DOUBLE | Award amount in USD. |
| `Awarding Agency` | VARCHAR | Agency name. |
| `Awarding Sub Agency` | VARCHAR | Sub-agency name. |
| `Contract Award Type` | VARCHAR | Contract type. |
| `NAICS` | STRUCT(code VARCHAR, description VARCHAR) | Industry code and description. |
| `PSC` | STRUCT(code VARCHAR, description VARCHAR) | Product or service code and description. |
| `awarding_agency_id` | DOUBLE | Agency id. |
| `agency_slug` | VARCHAR | Agency URL slug. |
| `generated_internal_id` | VARCHAR | Stable award id; staging de-duplicates on it. |
| `_batch_id` | VARCHAR | Batch id: the SHA-256 of the source file or payload. Lineage. |
| `_source` | VARCHAR | Source name (for a dataset, `dataset:<id>`). Lineage. |
| `_source_file` | INTEGER | File the row came from. Lineage. |
| `_loaded_at` | TIMESTAMPTZ | When the batch was loaded. Lineage. |

### `bronze.worldbank_lpi`

World Bank Logistics Performance Index, indicator `LP.LPI.OVRL.XQ`, all pages, including aggregates (removed in staging).

- **Type:** table
- **Grain:** One row per country or aggregate per year per batch.
- **Producer:** `worldbank_lpi` asset (`ingestion/sources/worldbank_lpi.py`).
- **Consumers:** `gate_worldbank_lpi`; `silver.stg_worldbank_lpi`.

| Column | Type | Meaning |
|---|---|---|
| `indicator` | STRUCT(id VARCHAR, "value" VARCHAR) | Indicator id and name. |
| `country` | STRUCT(id VARCHAR, "value" VARCHAR) | Country or aggregate id and name. |
| `countryiso3code` | VARCHAR | ISO3 code (blank for some aggregates). |
| `date` | VARCHAR | Year, as text. |
| `value` | DOUBLE | LPI score, 1 (low) to 5 (high); null in years with no survey. |
| `unit` | VARCHAR | API field; unused. |
| `obs_status` | VARCHAR | API field; unused. |
| `decimal` | BIGINT | API field; unused. |
| `_batch_id` | VARCHAR | Batch id: the SHA-256 of the source file or payload. Lineage. |
| `_source` | VARCHAR | Source name (for a dataset, `dataset:<id>`). Lineage. |
| `_source_file` | VARCHAR | File the row came from. Lineage. |
| `_loaded_at` | TIMESTAMPTZ | When the batch was loaded. Lineage. |

## Schema `silver`

Built by dbt from gate-approved bronze only: staging views, conformed facts, the SCD2 dimension and two seeds.

### `silver.country_iso3_map`

Seed: DataCo's Spanish country names to ISO3. Also the first lookup of the dataset country normaliser.

- **Type:** table
- **Grain:** One row per Spanish name.
- **Producer:** dbt seed `seeds/country_iso3_map.csv`.
- **Consumers:** `fact_shipments`, `fact_purchase_orders`, `ingestion/precheck.py`, `datasets/country.py` (the CSV).

| Column | Type | Meaning |
|---|---|---|
| `order_country_es` | VARCHAR | Spanish name, as DataCo spells it. |
| `iso3` | VARCHAR | ISO3 code. |
| `country_name_en` | VARCHAR | English name. |
| `mapping_confidence` | VARCHAR | `certain` or `uncertain`. |
| `notes` | VARCHAR | Why a mapping is uncertain. |

### `silver.dim_supplier_scd2`

Supplier history as SCD Type 2, rebuilt from the quarterly series. Only `delivery_band` opens a version, and a new band must hold for 2 eligible quarters.

- **Type:** table
- **Grain:** One row per (`supplier_key`, `version_number`).
- **Producer:** dbt `models/silver/dim_supplier_scd2.sql`.
- **Consumers:** `gold.mart_supplier_risk`, the agent's `get_supplier_history`, the console's names.

| Column | Type | Meaning |
|---|---|---|
| `supplier_key` | VARCHAR | Supplier proxy. |
| `supplier_name` | VARCHAR | Category name. |
| `delivery_band` | VARCHAR | `low` < 0.50 ≤ `medium` < 0.65 ≤ `high` late rate, or `unrated`. |
| `volume_band` | VARCHAR | `low` < 100 ≤ `medium` < 1000 ≤ `high` shipments; context only. |
| `row_hash` | VARCHAR | MD5 of the delivery band. |
| `effective_from` | DATE | First quarter of the version. |
| `effective_to` | DATE | Next version's start (exclusive); null when current. |
| `is_current` | BOOLEAN | Latest version. |
| `version_number` | INTEGER | 1, 2, ... |

### `silver.fact_purchase_orders`

Order-level roll-up of order items.

- **Type:** table
- **Grain:** One row per order (`order_id`).
- **Producer:** dbt `models/silver/fact_purchase_orders.sql`.
- **Consumers:** None downstream today; kept as a conformed fact with dbt tests.

| Column | Type | Meaning |
|---|---|---|
| `order_id` | BIGINT | Order id. |
| `order_date` | DATE | Order date. |
| `order_quarter` | VARCHAR | Quarter label. |
| `item_count` | BIGINT | Items in the order. |
| `order_total` | DOUBLE | Order total. |
| `order_country_iso3` | VARCHAR | Destination ISO3. |

### `silver.fact_shipments`

Canonical DataCo shipments with destination ISO3.

- **Type:** table
- **Grain:** One row per order item (`order_item_id`).
- **Producer:** dbt `models/silver/fact_shipments.sql`.
- **Consumers:** `gold.mart_supplier_risk`, `gold.mart_shipment_delay`, the agent's country and market tools.

| Column | Type | Meaning |
|---|---|---|
| `order_item_id` | BIGINT | Order item id. |
| `order_id` | BIGINT | Order id. |
| `supplier_key` | VARCHAR | Supplier proxy. |
| `order_date` | DATE | Order date. |
| `shipping_date` | DATE | Shipping date. |
| `order_quarter` | VARCHAR | Label such as 2017-Q1. |
| `days_real` | INTEGER | Actual days. |
| `days_scheduled` | INTEGER | Scheduled days. |
| `delay_days` | INTEGER | Actual minus scheduled days. |
| `late_flag` | BOOLEAN | Late, from `Late_delivery_risk`. |
| `delivery_status` | VARCHAR | Delivery status. |
| `shipping_mode` | VARCHAR | Shipping mode. |
| `order_country_iso3` | VARCHAR | Destination ISO3. |
| `market` | VARCHAR | Destination market. |
| `sales` | DOUBLE | Sales. |

### `silver.int_supplier_quarter_metrics`

Quarterly metrics per supplier. Cancelled shipments count as shipments but not toward late rate or delay.

- **Type:** view
- **Grain:** One row per (`supplier_key`, `quarter_start`) with shipments.
- **Producer:** dbt view `models/intermediate/int_supplier_quarter_metrics.sql`.
- **Consumers:** `dim_supplier_scd2`, `gold.mart_supplier_risk`.

| Column | Type | Meaning |
|---|---|---|
| `supplier_key` | VARCHAR | Supplier proxy. |
| `supplier_name` | VARCHAR | Category name. |
| `quarter_start` | DATE | First day of the quarter. |
| `order_quarter` | VARCHAR | Quarter label. |
| `shipment_count` | BIGINT | All shipments. |
| `canceled_count` | BIGINT | Cancelled shipments. |
| `late_count` | BIGINT | Late, not cancelled. |
| `late_rate` | DOUBLE | `late_count` / non-cancelled shipments. |
| `avg_delay_days` | DOUBLE | Mean delay, not cancelled. |
| `stddev_delay_days` | DOUBLE | Sample standard deviation of delay, not cancelled. |
| `low_volume` | BOOLEAN | Fewer than `min_quarter_shipments` (50). |
| `partial_quarter` | BOOLEAN | Data does not cover the quarter, or under 90% of its days have an order. |
| `day_coverage` | DOUBLE | Share of the quarter's days with an order. |

### `silver.stg_dataco_order_items`

Typed, gated DataCo items without personal data. Defines `supplier_key`: `cat_` plus the slugged category name.

- **Type:** view
- **Grain:** One row per order item, approved batches only.
- **Producer:** dbt view `models/staging/stg_dataco_order_items.sql`.
- **Consumers:** `fact_shipments`, `fact_purchase_orders`, `int_supplier_quarter_metrics`.

| Column | Type | Meaning |
|---|---|---|
| `order_item_id` | BIGINT | Order item id. |
| `order_id` | BIGINT | Order id. |
| `customer_id` | BIGINT | Customer id (no other customer data). |
| `supplier_key` | VARCHAR | Supplier proxy: `cat_<slug of category name>`. |
| `category_name` | VARCHAR | Category name. |
| `category_id` | BIGINT | Category id (one name has two ids). |
| `department_id` | BIGINT | Department id. |
| `department_name` | VARCHAR | Department. |
| `product_card_id` | BIGINT | Product id. |
| `product_name` | VARCHAR | Product. |
| `product_price` | DOUBLE | List price. |
| `product_status` | INTEGER | Product status code. |
| `order_ts` | TIMESTAMP | Order timestamp. |
| `shipping_ts` | TIMESTAMP | Shipping timestamp. |
| `days_real` | INTEGER | Actual shipping days. |
| `days_scheduled` | INTEGER | Scheduled shipping days. |
| `delivery_status` | VARCHAR | DataCo delivery status. |
| `late_flag` | BOOLEAN | `Late_delivery_risk` = 1. |
| `canceled_flag` | BOOLEAN | Delivery status is `Shipping canceled`. |
| `shipping_mode` | VARCHAR | Shipping mode. |
| `payment_type` | VARCHAR | Payment type. |
| `order_status` | VARCHAR | Order status. |
| `customer_segment` | VARCHAR | Customer segment. |
| `market` | VARCHAR | Destination market. |
| `order_region` | VARCHAR | Destination region. |
| `order_country_es` | VARCHAR | Destination country, Spanish name. |
| `order_state` | VARCHAR | Destination state. |
| `order_city` | VARCHAR | Destination city. |
| `quantity` | INTEGER | Units. |
| `item_price` | DOUBLE | Item price. |
| `discount` | DOUBLE | Discount amount. |
| `discount_rate` | DOUBLE | Discount rate. |
| `sales` | DOUBLE | Sales. |
| `item_total` | DOUBLE | Item total. |
| `profit_ratio` | DOUBLE | Item profit ratio. |
| `order_profit` | DOUBLE | Order profit. |
| `batch_id` | VARCHAR | Bronze batch. |
| `loaded_at` | TIMESTAMPTZ | Bronze load time. |

### `silver.stg_usaspending_awards`

Typed awards, one per `generated_internal_id` from the most recently loaded approved batch. Not linked to suppliers.

- **Type:** view
- **Grain:** One row per award.
- **Producer:** dbt view `models/staging/stg_usaspending_awards.sql`.
- **Consumers:** dbt tests only; no fact, mart or score.

| Column | Type | Meaning |
|---|---|---|
| `generated_internal_id` | VARCHAR | Stable award id (key). |
| `award_id` | VARCHAR | Public award id. |
| `recipient_name` | VARCHAR | Vendor. |
| `recipient_uei` | VARCHAR | Vendor UEI. |
| `recipient_id` | VARCHAR | API recipient id. |
| `start_date` | DATE | Award start. |
| `end_date` | DATE | Award end. |
| `award_amount` | DOUBLE | Amount in USD. |
| `awarding_agency` | VARCHAR | Agency. |
| `awarding_sub_agency` | VARCHAR | Sub-agency. |
| `awarding_agency_id` | INTEGER | Agency id. |
| `agency_slug` | VARCHAR | Agency slug. |
| `contract_award_type` | VARCHAR | Contract type. |
| `naics_code` | VARCHAR | Industry code. |
| `naics_description` | VARCHAR | Industry. |
| `psc_code` | VARCHAR | Product or service code. |
| `psc_description` | VARCHAR | Product or service. |
| `batch_id` | VARCHAR | Bronze batch. |
| `loaded_at` | TIMESTAMPTZ | Bronze load time. |
| `date_valid` | BOOLEAN | False when the end date is before the start date. |

### `silver.stg_worldbank_lpi`

LPI scores for real economies; aggregates removed through the `wb_country_reference` seed.

- **Type:** view
- **Grain:** One row per (`country_iso3`, `lpi_year`).
- **Producer:** dbt view `models/staging/stg_worldbank_lpi.sql`.
- **Consumers:** `gold.mart_supplier_risk`; `ds_<id>.mart_risk`; the agent's `get_country_risk`.

| Column | Type | Meaning |
|---|---|---|
| `country_iso3` | VARCHAR | ISO3 code. |
| `country_name` | VARCHAR | Country. |
| `lpi_year` | INTEGER | Survey year. |
| `lpi_score` | DOUBLE | Score, 1 to 5. |
| `indicator_id` | VARCHAR | `LP.LPI.OVRL.XQ`. |
| `batch_id` | VARCHAR | Bronze batch. |
| `loaded_at` | TIMESTAMPTZ | Bronze load time. |

### `silver.wb_country_reference`

Seed: the World Bank's economy and aggregate list.

- **Type:** table
- **Grain:** One row per ISO3 code.
- **Producer:** dbt seed `seeds/wb_country_reference.csv`.
- **Consumers:** `stg_worldbank_lpi`, `datasets/country.py` (the CSV).

| Column | Type | Meaning |
|---|---|---|
| `iso3` | VARCHAR | ISO3 code. |
| `iso2` | VARCHAR | ISO2 code. |
| `country_name` | VARCHAR | World Bank name. |
| `region` | VARCHAR | Region. |
| `income_level` | VARCHAR | Income group. |
| `is_aggregate` | BOOLEAN | True for regions and income groups, never matched. |

## Schema `gold`

Two dbt marts and the scorer's two tables. The scorer's tables have primary keys, so the restore point leaves them alone.

### `gold.mart_shipment_delay`

Shipment-level delay mart for ad hoc analysis.

- **Type:** table
- **Grain:** One row per shipment.
- **Producer:** dbt `models/gold/mart_shipment_delay.sql`.
- **Consumers:** None in code; dbt tests only.

| Column | Type | Meaning |
|---|---|---|
| `shipment_id` | BIGINT | Order item id. |
| `supplier_key` | VARCHAR | Supplier proxy. |
| `supplier_name` | VARCHAR | Category name. |
| `quarter` | VARCHAR | Quarter label. |
| `delay_days` | INTEGER | Actual minus scheduled days. |
| `late_flag` | BOOLEAN | Late. |
| `shipping_mode` | VARCHAR | Shipping mode. |
| `country` | VARCHAR | Destination. |

### `gold.mart_supplier_risk`

The scorer's input: eligible supplier-quarters with trailing, destination-based country risk.

- **Type:** table
- **Grain:** One row per (`supplier_key`, `quarter`).
- **Producer:** dbt `models/gold/mart_supplier_risk.sql`.
- **Consumers:** `risk_engine/scorer.py`, the agent's tools.

| Column | Type | Meaning |
|---|---|---|
| `supplier_key` | VARCHAR | Supplier proxy. |
| `supplier_name` | VARCHAR | Category name. |
| `quarter` | DATE | Quarter start. |
| `late_rate` | DOUBLE | Late rate. |
| `avg_delay_days` | DOUBLE | Mean delay. |
| `delay_stddev` | DOUBLE | Delay variability. |
| `shipment_count` | BIGINT | Shipments. |
| `eligible_quarters_to_date` | BIGINT | Eligible quarters so far for this supplier. |
| `delivery_band` | VARCHAR | SCD2 band in force. |
| `country_risk` | DOUBLE | (5 − LPI) / 4, shipment-weighted over the last 4 eligible quarters; latest LPI at or before the quarter, median for countries without one. |
| `country_coverage` | DOUBLE | Share of those shipments with a real LPI. |

### `gold.supplier_risk_flags`

Tier rises that passed hysteresis. A flag that stops qualifying is resolved, not deleted (flags of an older rules version are deleted).

- **Type:** table; primary key (supplier_key, quarter)
- **Grain:** One row per (`supplier_key`, `quarter`).
- **Producer:** `risk_engine/scorer.py`.
- **Consumers:** The API and console.

| Column | Type | Meaning |
|---|---|---|
| `supplier_key` | VARCHAR | Supplier proxy. |
| `quarter` | DATE | Quarter of the flag. |
| `old_tier` | VARCHAR | Tier before. |
| `new_tier` | VARCHAR | Tier after. |
| `score` | DOUBLE | Score. |
| `top_contributing_factor` | JSON | The factor behind the rise, with its numbers. |
| `confidence` | VARCHAR | `full` or `low`. |
| `event_id` | VARCHAR | The current event for this flag. |
| `status` | VARCHAR | `active` or `resolved`. |
| `resolved_at` | TIMESTAMPTZ | When it stopped qualifying. |
| `resolution` | VARCHAR | Why. |

### `gold.supplier_risk_score`

One rule-based score per eligible supplier-quarter: the source of truth.

- **Type:** table; primary key (supplier_key, quarter)
- **Grain:** One row per (`supplier_key`, `quarter`).
- **Producer:** `risk_engine/scorer.py`.
- **Consumers:** Flag replay, the agent's tools, the API.

| Column | Type | Meaning |
|---|---|---|
| `supplier_key` | VARCHAR | Supplier proxy. |
| `quarter` | DATE | Quarter start. |
| `score` | DOUBLE | Weighted sum, 0 to 1. |
| `tier` | VARCHAR | `low` < 0.38 ≤ `medium` < 0.68 ≤ `high`. |
| `contributing_factors` | JSON | Per factor: raw value, normalised value, weight, weighted contribution. |
| `confidence` | VARCHAR | `full`, or `low` under 3 eligible quarters or 95% country coverage. |
| `rules_version` | VARCHAR | Version of `rules.yaml`. |

## Schema `agent`

Events, the agent's investigations and proposals, their effects and the audit log. Written through `agent/store.py` (events by the scorer and the gate).

### `agent.actions`

What the agent proposed. Created only for a grounded report; takes effect only after a named human approves.

- **Type:** table; primary key (action_id)
- **Grain:** One row per `action_id`.
- **Producer:** `agent/approval.py`.
- **Consumers:** The Approvals screen and CLI.

| Column | Type | Meaning |
|---|---|---|
| `action_id` | VARCHAR | UUID. |
| `investigation_id` | VARCHAR | The grounded investigation. |
| `event_id` | VARCHAR | The event. |
| `supplier_key` | VARCHAR | Supplier. |
| `quarter` | DATE | Quarter. |
| `action_type` | VARCHAR | `no_action`, `watchlist` or `request_review`. |
| `status` | VARCHAR | `proposed`, `approved`, `rejected` or `applied`. |
| `proposed_at` | TIMESTAMPTZ | When proposed. |
| `decided_at` | TIMESTAMPTZ | When decided. |
| `decided_by` | VARCHAR | Reviewer name. |
| `reason` | VARCHAR | Reviewer's reason. |
| `applied_at` | TIMESTAMPTZ | When it took effect. |

### `agent.audit_log`

Hash-chained, append-only record of agent and human decisions. No update or delete helper exists; `verify_audit_log` recomputes the chain.

- **Type:** table; primary key (seq)
- **Grain:** One row per `seq`.
- **Producer:** `store.append_audit`, called by `agent/run.py`, `agent/approval.py`, `datasets/mapping_store.py`, `datasets/score.py`, `datasets/bronze.py` (a quarantined upload) and `agent/repair_noise_events.py`.
- **Consumers:** The Pipeline screen, `/api/audit`, `approve --audit`.

| Column | Type | Meaning |
|---|---|---|
| `seq` | BIGINT | 1, 2, ... |
| `ts` | TIMESTAMPTZ | When. |
| `actor` | VARCHAR | `agent`, a reviewer's name, or a component. |
| `event` | VARCHAR | For example `investigation_proposed`, `action_approved`, `mapping_edited`, `dataset_scored`. |
| `detail` | JSON | JSON facts. |
| `prev_hash` | VARCHAR | Previous row's hash (64 zeros for the first). |
| `row_hash` | VARCHAR | SHA-256 over this row and `prev_hash`. |

### `agent.events`

Everything the agent can be asked about: risk flags and failed gates. Append-only for flags: a changed payload supersedes the old event and creates a new one.

- **Type:** table; primary key (event_id)
- **Grain:** One row per `event_id`.
- **Producer:** `risk_engine/scorer.py` (`risk_flag`), `data_quality/gate.py` and `datasets/bronze.py` (`dq_failure`).
- **Consumers:** The agent, the sensor, the API.

| Column | Type | Meaning |
|---|---|---|
| `event_id` | VARCHAR | `risk_flag:<rules>:<quarter>:<supplier>`, with `:p<hash>` after a supersede; datasets prefix `ds_<id>:`. |
| `event_type` | VARCHAR | `risk_flag` or `dq_failure`. |
| `source` | VARCHAR | `risk_engine`, `dataset_risk_engine`, or the failing source. |
| `batch_id` | VARCHAR | Quarter for flags, batch for failures. |
| `created_at` | TIMESTAMPTZ | When recorded. |
| `payload` | JSON | The flag's or failure's facts. |
| `resolved_at` | TIMESTAMPTZ | Set when superseded or resolved. |
| `resolution` | VARCHAR | Why. |
| `investigation_status` | VARCHAR | `open`, `queued` or `investigated`. |

### `agent.investigations`

Every agent run on an event, grounded or not, with its tool calls.

- **Type:** table; primary key (investigation_id)
- **Grain:** One row per `investigation_id`; an event can have several.
- **Producer:** `agent/run.py` (`record_outcome`), through `store.writer`.
- **Consumers:** Recall memory, the API, the console.

| Column | Type | Meaning |
|---|---|---|
| `investigation_id` | VARCHAR | UUID. |
| `event_id` | VARCHAR | The event. |
| `report` | JSON | The report JSON (null when there was none). |
| `status` | VARCHAR | `proposed`, `rejected_ungrounded` or `no_report`. |
| `grounded` | BOOLEAN | Every number traced to a tool result. |
| `grounded_ratio` | DOUBLE | Share of numbers grounded. |
| `ungrounded_numbers` | JSON | The numbers that were not. |
| `tool_calls` | JSON | Each call, its arguments and result. |
| `steps` | INTEGER | Model calls. |
| `prompt_tokens` | INTEGER | Prompt tokens. |
| `completion_tokens` | INTEGER | Completion tokens. |
| `total_tokens` | INTEGER | Sum. |
| `model` | VARCHAR | Model name. |
| `requires_approval` | BOOLEAN | The setting at the time. |
| `created_at` | TIMESTAMPTZ | When recorded. |
| `human_decision` | VARCHAR | Written back on approve or reject, for recall. |
| `decision_reason` | VARCHAR | The reviewer's reason. |
| `decided_by` | VARCHAR | The reviewer. |
| `decided_at` | TIMESTAMPTZ | When decided. |
| `dataset_id` | VARCHAR | Null for DataCo, otherwise the dataset. |

### `agent.review_requests`

Effect of an applied `request_review` action.

- **Type:** table; primary key (action_id)
- **Grain:** One row per action.
- **Producer:** `approval.apply_action`.
- **Consumers:** Reviewers; nothing reads it automatically.

| Column | Type | Meaning |
|---|---|---|
| `action_id` | VARCHAR | The action. |
| `event_id` | VARCHAR | The event. |
| `supplier_key` | VARCHAR | Supplier. |
| `quarter` | DATE | Quarter. |
| `requested_by` | VARCHAR | The approver. |
| `reason` | VARCHAR | The reason given. |
| `requested_at` | TIMESTAMPTZ | When applied. |

### `agent.watchlist`

Effect of an applied `watchlist` action.

- **Type:** table; primary key (action_id)
- **Grain:** One row per action.
- **Producer:** `approval.apply_action`, the only place an action takes effect.
- **Consumers:** Reviewers; nothing reads it automatically.

| Column | Type | Meaning |
|---|---|---|
| `action_id` | VARCHAR | The action. |
| `event_id` | VARCHAR | The event. |
| `supplier_key` | VARCHAR | Supplier. |
| `quarter` | DATE | Quarter. |
| `added_by` | VARCHAR | The approver. |
| `added_at` | TIMESTAMPTZ | When applied. |

## Schema `meta`

Registry for onboarded datasets. Written only through `datasets/registry.py` and `datasets/mapping_store.py`.

### `meta.dataset_columns`

The profile of each column. Only names recorded here can appear in generated SQL.

- **Type:** table; primary key (dataset_id, ordinal)
- **Grain:** One row per (`dataset_id`, `ordinal`).
- **Producer:** `registry.record_profile`.
- **Consumers:** Rules, mappers, validator, Profile screen.

| Column | Type | Meaning |
|---|---|---|
| `dataset_id` | VARCHAR | Dataset. |
| `ordinal` | INTEGER | Position. |
| `column_name` | VARCHAR | Name as in the file. |
| `inferred_type` | VARCHAR | `string`, `int`, `float`, `bool` or `date`. |
| `type_share` | DOUBLE | Share of non-empty values of that type. |
| `null_pct` | DOUBLE | Percent empty, rounded. |
| `distinct_count` | BIGINT | Distinct values. |
| `min_value` | VARCHAR | Minimum (numeric and date columns only). |
| `max_value` | VARCHAR | Maximum (numeric and date columns only). |
| `is_candidate_key` | BOOLEAN | Could identify a row. |
| `looks_like_date` | BOOLEAN | Parsed as dates. |
| `date_format` | VARCHAR | Detected format. |
| `is_mixed_type` | BOOLEAN | Below 100% of its type. |
| `is_mostly_null` | BOOLEAN | Mostly empty. |

### `meta.dataset_rules`

Scoring rules frozen at a dataset's first scoring: bounds, weights, exclusions. Reused until `--refreeze`, which appends a version.

- **Type:** table; primary key (dataset_id, version)
- **Grain:** One row per (`dataset_id`, `version`).
- **Producer:** `registry.freeze_rules`, called by `datasets/score.py`.
- **Consumers:** Scoring, results.

| Column | Type | Meaning |
|---|---|---|
| `dataset_id` | VARCHAR | Dataset. |
| `version` | INTEGER | 1, 2, ... |
| `rules_version` | VARCHAR | `<rules.yaml version>-ds<n>`, such as `1.2-ds1`. |
| `bounds_mode` | VARCHAR | `dataset` (p5 and p95) or `flagship` (`rules.yaml`). |
| `mapping_id` | VARCHAR | Mapping it was frozen for. |
| `rules` | JSON | The effective rules JSON. |
| `created_at` | TIMESTAMPTZ | When frozen. |

### `meta.datasets`

Registry of onboarded files. A second upload of the same bytes is reported as already registered.

- **Type:** table; primary key (dataset_id); unique (file_sha256)
- **Grain:** One row per `dataset_id` (first 12 hex characters of the file's SHA-256); `file_sha256` is unique.
- **Producer:** `datasets/registry.py` (the only writer for `meta.*`).
- **Consumers:** The console, onboarding, mapping, scoring.

| Column | Type | Meaning |
|---|---|---|
| `dataset_id` | VARCHAR | 12 hex characters. |
| `name` | VARCHAR | Display name. |
| `source_filename` | VARCHAR | File name (generated for console uploads). |
| `file_sha256` | VARCHAR | File hash. |
| `status` | VARCHAR | `uploaded`, `profiled`, `gated`, `quarantined`, `failed` or `mapped`. The console shows `scored` for a mapped dataset with frozen rules; it is not stored. |
| `created_at` | TIMESTAMPTZ | Registered. |
| `updated_at` | TIMESTAMPTZ | Last change. |
| `row_count` | BIGINT | Rows. |
| `column_count` | INTEGER | Columns. |
| `schema_name` | VARCHAR | `ds_<dataset_id>`. |
| `encoding` | VARCHAR | Detected encoding. |
| `delimiter` | VARCHAR | Detected delimiter. |
| `status_reason` | VARCHAR | Why quarantined or failed. |
| `profile_warnings` | JSON | Profile warnings. |
| `suite` | JSON | Generated rules. |
| `gate_report` | JSON | Rule-by-rule gate result. |

### `meta.mappings`

Every mapping version. An edit adds a version and supersedes the old one; nothing is overwritten.

- **Type:** table; primary key (mapping_id); unique (dataset_id, version)
- **Grain:** One row per `mapping_id`; (`dataset_id`, `version`) is unique.
- **Producer:** `datasets/mapping_store.py`.
- **Consumers:** Mapping review, scoring.

| Column | Type | Meaning |
|---|---|---|
| `mapping_id` | VARCHAR | UUID. |
| `dataset_id` | VARCHAR | Dataset. |
| `version` | INTEGER | 1, 2, ... |
| `proposal` | JSON | The mapping JSON. |
| `source` | VARCHAR | `agent`, `heuristic` or `human`. |
| `status` | VARCHAR | `proposed`, `approved`, `rejected` or `superseded`. |
| `mappable` | BOOLEAN | Every required field maps to a valid column. |
| `scoreable` | BOOLEAN | Estimate: at least 3 eligible quarters and most entities near 50 rows a quarter. |
| `reasons` | JSON | Plain-language reasons for both. |
| `validation` | JSON | Validator output, decision path, escalation reasons, fallback reason. |
| `created_by` | VARCHAR | Mapper or reviewer. |
| `created_at` | TIMESTAMPTZ | When. |
| `prompt_tokens` | INTEGER | Agent tokens. |
| `completion_tokens` | INTEGER | Agent tokens. |
| `decided_by` | VARCHAR | Reviewer. |
| `reason` | VARCHAR | Reviewer's reason. |
| `decided_at` | TIMESTAMPTZ | When decided. |
| `superseded_by` | VARCHAR | The version that replaced it. |

### `meta.runs`

Scoring runs. A live run's stage is in a status file (DuckDB cannot be read during a write); the final row is written with the scores, or by the runner on failure. Agent investigation runs use status files only.

- **Type:** table; primary key (run_id)
- **Grain:** One row per `run_id`.
- **Producer:** `datasets/score.py`, `datasets/runs.py`.
- **Consumers:** `/api/runs/{id}`, results.

| Column | Type | Meaning |
|---|---|---|
| `run_id` | VARCHAR | UUID. |
| `dataset_id` | VARCHAR | Dataset. |
| `stage` | VARCHAR | `silver`, `metrics`, `scoring` or `flags`. |
| `status` | VARCHAR | `queued`, `running`, `succeeded` or `failed`. |
| `trigger` | VARCHAR | `api`, `cli` or `dagster`. |
| `started_at` | TIMESTAMPTZ | Start. |
| `finished_at` | TIMESTAMPTZ | End. |
| `error` | VARCHAR | Plain-language error. |
| `summary` | JSON | Counts, country match, factors, bounds. |

## Schema `ds_<id>`

One schema per onboarded dataset; `<id>` is the first 12 hex characters of the file's SHA-256. A gated dataset has only `bronze_raw`; the others appear at its first scoring. Column types below are from a synthetic example.

### `ds_<id>.bronze_raw`

The uploaded file, every column as text, plus lineage. Data columns vary per dataset; those shown come from the synthetic example.

- **Type:** table
- **Grain:** One row per file row.
- **Producer:** `datasets/bronze.py`, after the gate passes.
- **Consumers:** `validator.load_context`, `build_silver`.

| Column | Type | Meaning |
|---|---|---|
| `shipment_id` | VARCHAR | Example column. |
| `supplier` | VARCHAR | Example column. |
| `order_date` | VARCHAR | Example column. |
| `planned_hours` | VARCHAR | Example column. |
| `actual_hours` | VARCHAR | Example column. |
| `destination_country` | VARCHAR | Example column. |
| `status` | VARCHAR | Example column. |
| `_batch_id` | VARCHAR | Batch id: the SHA-256 of the source file or payload. Lineage. |
| `_source` | VARCHAR | Source name (for a dataset, `dataset:<id>`). Lineage. |
| `_source_file` | VARCHAR | File the row came from. Lineage. |
| `_loaded_at` | TIMESTAMPTZ | When the batch was loaded. Lineage. |

### `ds_<id>.silver_shipments`

The canonical shipment table every dataset is mapped onto.

- **Type:** table
- **Grain:** One row per bronze row that parsed.
- **Producer:** `datasets/build_silver.py`.
- **Consumers:** `quarter_metrics`, `mart_risk`, the agent's country tool.

| Column | Type | Meaning |
|---|---|---|
| `entity_key` | VARCHAR | Lower-cased name with other characters replaced by `_`, at most 64 characters. |
| `entity_name` | VARCHAR | Name as in the file. |
| `event_date` | DATE | Parsed date. |
| `quarter` | DATE | Quarter start. |
| `is_late` | BOOLEAN | Actual > scheduled, delay > 0, or the flag's `late_when`. |
| `delay_days` | DOUBLE | In days; null for a late flag. |
| `is_cancelled` | BOOLEAN | Matches the mapping's cancelled rule. |
| `country_iso3` | VARCHAR | From `country_map`; null when unmatched. |
| `_batch_id` | VARCHAR | Bronze batch. |

### `ds_<id>.silver_rejects`

Rows that did not parse, with the first reason that applied.

- **Type:** table
- **Grain:** One row per rejected row.
- **Producer:** `datasets/build_silver.py`.
- **Consumers:** Results screen.

| Column | Type | Meaning |
|---|---|---|
| `bronze_rowid` | BIGINT | Row in `bronze_raw`. |
| `_batch_id` | VARCHAR | Bronze batch. |
| `reason` | VARCHAR | For example `entity is empty`. |
| `entity_value` | VARCHAR | Raw entity. |
| `date_value` | VARCHAR | Raw date. |
| `outcome_value` | VARCHAR | Raw outcome value. |
| `outcome_value_2` | VARCHAR | Second raw value for a scheduled/actual pair. |

### `ds_<id>.country_map`

Each distinct country value and its ISO3 code.

- **Type:** table; primary key (raw_value)
- **Grain:** One row per `raw_value`.
- **Producer:** `datasets/build_silver.py` with `datasets/country.py`.
- **Consumers:** `silver_shipments`.

| Column | Type | Meaning |
|---|---|---|
| `raw_value` | VARCHAR | Value in the file. |
| `iso3` | VARCHAR | ISO3; null when unmatched. |

### `ds_<id>.quarter_metrics`

Same definitions as `silver.int_supplier_quarter_metrics`.

- **Type:** table
- **Grain:** One row per (`entity_key`, `quarter_start`).
- **Producer:** `datasets/metrics.py`.
- **Consumers:** `mart_risk`.

| Column | Type | Meaning |
|---|---|---|
| `entity_key` | VARCHAR | Entity. |
| `entity_name` | VARCHAR | Name. |
| `quarter_start` | DATE | Quarter start. |
| `order_quarter` | VARCHAR | Quarter label. |
| `shipment_count` | BIGINT | All rows. |
| `canceled_count` | BIGINT | Cancelled rows. |
| `late_count` | BIGINT | Late, not cancelled. |
| `late_rate` | DOUBLE | Late / non-cancelled. |
| `avg_delay_days` | DOUBLE | Mean delay. |
| `stddev_delay_days` | DOUBLE | Delay variability. |
| `low_volume` | BOOLEAN | Under 50 rows. |
| `partial_quarter` | BOOLEAN | Data does not cover the quarter. |
| `day_coverage` | DOUBLE | Share of days with a row. |

### `ds_<id>.mart_risk`

Same definitions as `gold.mart_supplier_risk`, without the SCD2 band. Country columns are null when country risk is left out.

- **Type:** table
- **Grain:** One row per (`entity_key`, `quarter`).
- **Producer:** `datasets/metrics.py`.
- **Consumers:** `datasets/score.py`, agent tools, results.

| Column | Type | Meaning |
|---|---|---|
| `entity_key` | VARCHAR | Entity. |
| `entity_name` | VARCHAR | Name. |
| `quarter` | DATE | Quarter start. |
| `late_rate` | DOUBLE | Late rate. |
| `avg_delay_days` | DOUBLE | Mean delay. |
| `delay_stddev` | DOUBLE | Delay variability. |
| `shipment_count` | BIGINT | Rows. |
| `eligible_quarters_to_date` | BIGINT | Eligible quarters so far. |
| `country_risk` | DOUBLE | Trailing country risk, or null. |
| `country_coverage` | DOUBLE | Share with a real LPI, or null. |

### `ds_<id>.risk_score`

Same shape as `gold.supplier_risk_score`. An excluded factor appears in `contributing_factors` with `excluded: true` and its reason.

- **Type:** table; primary key (entity_key, quarter)
- **Grain:** One row per (`entity_key`, `quarter`).
- **Producer:** `risk_engine/scorer.py` through `datasets/score.py`.
- **Consumers:** Results, agent tools.

| Column | Type | Meaning |
|---|---|---|
| `entity_key` | VARCHAR | Entity. |
| `quarter` | DATE | Quarter start. |
| `score` | DOUBLE | 0 to 1; comparable within this dataset only. |
| `tier` | VARCHAR | `low`, `medium` or `high`. |
| `contributing_factors` | JSON | Per factor, including exclusions. |
| `confidence` | VARCHAR | `low` whenever a factor is excluded. |
| `rules_version` | VARCHAR | `meta.dataset_rules.rules_version`. |

### `ds_<id>.risk_flags`

Same shape as `gold.supplier_risk_flags`, without a primary key: a refreeze deletes and re-inserts a key in one transaction, which DuckDB's index check refuses. The scorer keeps one row per key.

- **Type:** table
- **Grain:** One row per (`entity_key`, `quarter`), by code.
- **Producer:** `risk_engine/scorer.py` through `datasets/score.py`.
- **Consumers:** Results, `/api/flags?dataset=`.

| Column | Type | Meaning |
|---|---|---|
| `entity_key` | VARCHAR | Entity. |
| `quarter` | DATE | Quarter. |
| `old_tier` | VARCHAR | Tier before. |
| `new_tier` | VARCHAR | Tier after. |
| `score` | DOUBLE | Score. |
| `top_contributing_factor` | JSON | The factor behind the rise. |
| `confidence` | VARCHAR | `full` or `low`. |
| `event_id` | VARCHAR | `ds_<id>:risk_flag:...`. |
| `status` | VARCHAR | `active` or `resolved`. |
| `resolved_at` | TIMESTAMPTZ | When resolved. |
| `resolution` | VARCHAR | Why. |

## Files outside the warehouse

| Path | What |
|---|---|
| `data/raw/` | The DataCo CSV (manual download) and the cached USASpending sample. Git-ignored. |
| `data/bronze/<source>/batch_id=<hash>/` | Parquet per batch. |
| `data/quarantine/<source>/` | Batches the gate refused, with `validation_result.json`. |
| `data/datasets/<id>/` | An onboarded dataset's Parquet. |
| `data/datasets/incoming/` | Uploads, deleted once onboarded. |
| `data/datasets/runs/<run_id>.json`, `.log` | Live status and log of a background run. |
| `data/quarantine/datasets/<id>/` | A quarantined dataset's batch and `gate_report.json`. |
