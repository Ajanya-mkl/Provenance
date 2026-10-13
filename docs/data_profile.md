# DataCo Supply Chain Dataset Profile

Profiled `data/raw/DataCoSupplyChainDataset.csv` locally with pandas, using
`encoding="latin-1"` and no data cleaning or type conversions to the source.

## Dataset summary

- Rows: **180,519**
- Columns: **53**
- Fully duplicated rows: **0**
- Candidate row key: `Order Item Id` is unique across all 180,519 rows.
- `Order Id` is not a row key: it has 65,752 distinct values and 114,767
  repeated occurrences, consistent with orders containing multiple items.
- Date ranges, parsed for profiling only:
  - `order date (DateOrders)`: 2015-01-01 00:00 through 2018-01-31 23:38
  - `shipping date (DateOrders)`: 2015-01-03 00:00 through 2018-02-06 22:14

## Dtypes and null counts

Dtypes below are those inferred by pandas from the CSV; the source values have
not been normalized.

| Column | Inferred dtype | Null count |
|---|---:|---:|
| `Type` | object | 0 |
| `Days for shipping (real)` | int64 | 0 |
| `Days for shipment (scheduled)` | int64 | 0 |
| `Benefit per order` | float64 | 0 |
| `Sales per customer` | float64 | 0 |
| `Delivery Status` | object | 0 |
| `Late_delivery_risk` | int64 | 0 |
| `Category Id` | int64 | 0 |
| `Category Name` | object | 0 |
| `Customer City` | object | 0 |
| `Customer Country` | object | 0 |
| `Customer Email` | object | 0 |
| `Customer Fname` | object | 0 |
| `Customer Id` | int64 | 0 |
| `Customer Lname` | object | 8 |
| `Customer Password` | object | 0 |
| `Customer Segment` | object | 0 |
| `Customer State` | object | 0 |
| `Customer Street` | object | 0 |
| `Customer Zipcode` | float64 | 3 |
| `Department Id` | int64 | 0 |
| `Department Name` | object | 0 |
| `Latitude` | float64 | 0 |
| `Longitude` | float64 | 0 |
| `Market` | object | 0 |
| `Order City` | object | 0 |
| `Order Country` | object | 0 |
| `Order Customer Id` | int64 | 0 |
| `order date (DateOrders)` | object | 0 |
| `Order Id` | int64 | 0 |
| `Order Item Cardprod Id` | int64 | 0 |
| `Order Item Discount` | float64 | 0 |
| `Order Item Discount Rate` | float64 | 0 |
| `Order Item Id` | int64 | 0 |
| `Order Item Product Price` | float64 | 0 |
| `Order Item Profit Ratio` | float64 | 0 |
| `Order Item Quantity` | int64 | 0 |
| `Sales` | float64 | 0 |
| `Order Item Total` | float64 | 0 |
| `Order Profit Per Order` | float64 | 0 |
| `Order Region` | object | 0 |
| `Order State` | object | 0 |
| `Order Status` | object | 0 |
| `Order Zipcode` | float64 | 155,679 |
| `Product Card Id` | int64 | 0 |
| `Product Category Id` | int64 | 0 |
| `Product Description` | float64 | 180,519 |
| `Product Image` | object | 0 |
| `Product Name` | object | 0 |
| `Product Price` | float64 | 0 |
| `Product Status` | int64 | 0 |
| `shipping date (DateOrders)` | object | 0 |
| `Shipping Mode` | object | 0 |

## USASpending sample profile

The current bronze sample has **10,000 rows**. `generated_internal_id` is the
unique record key (**10,000 distinct values in 10,000 rows**). `Award ID` is
not unique (**8,991 distinct values**). There are 329 repeated Award IDs (1,338
rows across those IDs); 307 repeated IDs have multiple recipient names, 326
have multiple amounts, and 87 have multiple contract award types. No repeated
ID has multiple awarding agencies. These are legitimate records, not duplicate
rows: **do not deduplicate in bronze, and never join on `Award ID` alone**.

Recipient UEI has **no nulls** and **4,244 distinct values**; recipient name has
**4,161 distinct values**. No UEI is associated with more than one name in
this sample. Key recipient matching on UEI, never on name.

`Start Date` ranges from **1978-09-15** to **2028-09-29**. Eight awards start
before 2000 and 95 start after the profile date (**2026-10-11**). These date
extremes are plausible; Phase 3 must treat them as warnings, not failures.
There is no attribute change history in this sample, so USASpending cannot
supply real SCD2 history.

Eight of the 10,000 sampled rows (0.08%) have `Start Date` after `End Date`.
They remain unchanged in bronze and are recorded as validation warnings under
the 99% date-order tolerance. Silver must flag these records with a
date-validity column and exclude them from any time-based logic.

**The eight reversed-date rows are systematic, not scattered** (checked
2026-10-13 against bronze batch `36f1f466…`):

| `generated_internal_id` | Recipient Name | Awarding Agency | Contract Award Type | Start Date | End Date |
|---|---|---|---|---|---|
| `CONT_AWD_HC101326FC111_9700_HC101322D0005_9700` | TRACE SYSTEMS INC. | Department of Defense | DELIVERY ORDER | 2026-10-05 | 2026-09-23 |
| `CONT_AWD_HC101326FC098_9700_HC101319D0004_9700` | GENERAL DYNAMICS MISSION SYSTEMS, INC. | Department of Defense | DELIVERY ORDER | 2026-09-21 | 2026-09-17 |
| `CONT_AWD_HC101326FB991_9700_HC101322D0005_9700` | TRACE SYSTEMS INC. | Department of Defense | DELIVERY ORDER | 2026-09-17 | 2026-09-03 |
| `CONT_AWD_HC101326FB967_9700_HC101322D0005_9700` | TRACE SYSTEMS INC. | Department of Defense | DELIVERY ORDER | 2026-09-03 | 2026-09-01 |
| `CONT_AWD_HC101326FB880_9700_HC101322D0005_9700` | TRACE SYSTEMS INC. | Department of Defense | DELIVERY ORDER | 2026-08-31 | 2026-08-21 |
| `CONT_AWD_HC101326FB877_9700_HC101322D0005_9700` | TRACE SYSTEMS INC. | Department of Defense | DELIVERY ORDER | 2026-08-28 | 2026-08-27 |
| `CONT_AWD_80HQTR26F7070_8000_80HQTR21D0003_8000` | AGILE DECISION SCIENCES, LLC | National Aeronautics and Space Administration | DELIVERY ORDER | 2026-04-01 | 2026-03-31 |
| `CONT_AWD_80HQTR26F7065_8000_80HQTR21D0003_8000` | AGILE DECISION SCIENCES, LLC | National Aeronautics and Space Administration | DELIVERY ORDER | 2026-04-01 | 2026-03-31 |

- All eight are `DELIVERY ORDER` (about 50% of the batch).
- Six are Department of Defense, although DoD is only 3.5% of the batch.
- Five are TRACE SYSTEMS orders under one parent contract, `HC101322D0005`.
- The two NASA orders share parent contract `80HQTR21D0003` and are reversed by exactly one day.
- The reversals are 1–14 days long.

This pattern points to how the source records dates for orders under these parent contracts, not to random entry errors. The 99% tolerance still applies: one parent contract can explain most of the reversed dates without the batch being corrupted.

The loader's request uses award types `A`, `B`, `C`, and `D`, and a page limit
of **100**; it continues fetching while `page_metadata.hasNext` is true, with
no configured total-row cap. The request associated with this sample filters
award types `A`, `B`, `C`, and `D` to **2026-09-01 through 2026-09-30**, with
`limit=100` per page. The cached first page contains 100 rows, has
`hasNext=true`, and all 100 `generated_internal_id` values are present in the
bronze sample. Pagination produced the **10,000-row** table; 100 is a page size,
not a total limit. The loader does not impose a deliberate 10,000-row cap.

## DataCo supplier-proxy candidates

DataCo has no true supplier, vendor, or company identifier. The values below
are operational dimensions that could be chosen as a **supplier proxy**, not
source-backed supplier identities. A shipment here means one DataCo dataset
row (order-item record). “Per value” gives the mean and min–max record count
across distinct values. “Per value-quarter” gives the same distribution over
observed value/quarter pairs, grouping `order date (DateOrders)` into calendar
quarters (13 quarters from 2015 Q1 through 2018 Q1).

| Candidate column | Distinct values | Shipment rows per value (mean; min–max) | Shipment rows per value-quarter (mean; min–max) |
|---|---:|---:|---:|
| `Department Name` | 11 | 16,410.82; 362–66,861 | 2,149.04; 34–6,170 |
| `Market` | 5 | 36,103.80; 11,614–51,594 | 6,447.11; 118–15,812 |
| `Order Region` | 23 | 7,848.65; 553–28,341 | 1,842.03; 8–9,268 |
| `Category Name` | 50 | 3,610.38; 61–24,551 | 573.08; 3–2,275 |
| `Product Name` | 118 | 1,529.82; 10–24,515 | 269.83; 2–2,275 |
| `Shipping Mode` | 4 | 45,129.75; 9,737–107,752 | 3,471.52; 120–9,490 |
| `Order Country` | 164 | 1,100.73; 1–24,840 | 344.50; 1–15,294 |
| `Customer Segment` | 3 | 60,173.00; 32,226–93,504 | 4,628.69; 435–8,371 |

**Recommendation for the user's choice: `Product Name`.** Among these
dimensions it offers the most granular product-level proxy (118 values) while
still having repeated records for periodic shipment metrics. It remains a
product, not a supplier; no cross-source linkage to USASpending is established.
Department, market, region, category, shipping mode, and customer segment are
broader alternative groupings. No supplier/linkage selection is recorded
here. If chosen, SCD2 history should be derived from periodic snapshots of
DataCo order dates, including supplier-proxy metrics and a risk tier per
period—not from USASpending.

## Supplier history cadence: quarterly

**Decision:** supplier (`Category Name`) history is tracked **quarterly, not
monthly**. Monthly late-delivery rates would be too noisy and would create
false risk-tier changes.

Measured on the bronze DataCo batch (180,519 rows, 2026-10-13):

- Order dates run from **2015-01-01 00:00** to **2018-01-31 23:38**, which is
  **13 calendar quarters** (2015Q1–2018Q1).
- **50** categories produce **315** observed category-quarters, out of 650
  possible. Each category is active in a median of **3** quarters (range
  1–12), and none is active in all 13.
- Shipments per category-quarter: min **3**, median **134**, max **2,275**
  (mean 573). **40** category-quarters (12.7%) have fewer than 30 shipments.
- Shipments per category-month: median **48**, and **39.9%** are under 30.
  This is why monthly tracking is rejected.
- **2015Q1 is complete** (orders on all 90 days). **2018Q1 is partial**
  (January only, 2,123 rows). 2017Q4 covers full calendar dates but has only
  6,434 rows, against about 15,500 in earlier quarters.

The original estimate was about 300 shipments per category per quarter, with
every category present each quarter. The measured median is 134, and coverage
is sparse. Phase 4 must therefore:

- flag category-quarters under 30 shipments as low-volume instead of letting
  them drive a tier change;
- flag or exclude partial 2018Q1 and thin 2017Q4;
- not treat a missing quarter as a change in attributes.

`Order Country` values are in **Spanish** (164 values, for example
`Estados Unidos`, `Francia`, `México`, `Alemania`, `Brasil`, `Reino Unido`).
None of the English forms appears. `Customer Country` is also Spanish
(`EE. UU.`, `Puerto Rico`). Joining to World Bank LPI requires a
Spanish-name-to-ISO3 mapping.
