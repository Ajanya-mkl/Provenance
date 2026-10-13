-- No silver relation exposes DataCo personal-data columns, under either
-- their bronze names or a snake_case rename. The refs make this test run after
-- every silver relation exists.
-- depends_on: {{ ref('stg_dataco_order_items') }}
-- depends_on: {{ ref('stg_usaspending_awards') }}
-- depends_on: {{ ref('stg_worldbank_lpi') }}
-- depends_on: {{ ref('int_supplier_quarter_metrics') }}
-- depends_on: {{ ref('fact_shipments') }}
-- depends_on: {{ ref('fact_purchase_orders') }}
-- depends_on: {{ ref('dim_supplier_scd2') }}
select table_name, column_name
from information_schema.columns
where table_schema = '{{ target.schema }}'
  and regexp_matches(
      lower(column_name),
      '^(customer_)?(email|password|fname|lname|first_name|last_name|street|zipcode)$'
      || '|^customer (email|password|fname|lname|street|zipcode)$'
      || '|^product[ _](image|description)$'
  )
