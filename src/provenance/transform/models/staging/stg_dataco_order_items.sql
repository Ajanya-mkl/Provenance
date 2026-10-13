-- One row per DataCo order item from gate-approved batches.
-- Personal data (customer email, password, first/last name, street, zipcode)
-- and the product image/description columns are deliberately not selected.
with approved_batches as (
    select batch_id
    from {{ source('bronze', 'dq_results') }}
    where source = 'dataco_shipments'
      and status in ('{{ var("approved_gate_statuses") | join("', '") }}')
)

select
    "Order Item Id"::bigint                                         as order_item_id,
    "Order Id"::bigint                                              as order_id,
    "Customer Id"::bigint                                           as customer_id,
    -- Supplier proxy = category name (one name has two Category Ids).
    'cat_' || trim(
        regexp_replace(lower(trim("Category Name")), '[^a-z0-9]+', '_', 'g'), '_'
    )                                                               as supplier_key,
    "Category Name"                                                 as category_name,
    "Category Id"::bigint                                           as category_id,
    "Department Id"::bigint                                         as department_id,
    "Department Name"                                               as department_name,
    "Product Card Id"::bigint                                       as product_card_id,
    "Product Name"                                                  as product_name,
    "Product Price"::double                                         as product_price,
    "Product Status"::integer                                       as product_status,
    strptime("order date (DateOrders)", '%m/%d/%Y %H:%M')           as order_ts,
    strptime("shipping date (DateOrders)", '%m/%d/%Y %H:%M')        as shipping_ts,
    "Days for shipping (real)"::integer                             as days_real,
    "Days for shipment (scheduled)"::integer                        as days_scheduled,
    "Delivery Status"                                               as delivery_status,
    "Late_delivery_risk"::integer = 1                               as late_flag,
    "Delivery Status" = 'Shipping canceled'                         as canceled_flag,
    "Shipping Mode"                                                 as shipping_mode,
    "Type"                                                          as payment_type,
    "Order Status"                                                  as order_status,
    "Customer Segment"                                              as customer_segment,
    "Market"                                                        as market,
    "Order Region"                                                  as order_region,
    "Order Country"                                                 as order_country_es,
    "Order State"                                                   as order_state,
    "Order City"                                                    as order_city,
    "Order Item Quantity"::integer                                  as quantity,
    "Order Item Product Price"::double                              as item_price,
    "Order Item Discount"::double                                   as discount,
    "Order Item Discount Rate"::double                              as discount_rate,
    "Sales"::double                                                 as sales,
    "Order Item Total"::double                                      as item_total,
    "Order Item Profit Ratio"::double                               as profit_ratio,
    "Order Profit Per Order"::double                                as order_profit,
    _batch_id                                                       as batch_id,
    _loaded_at                                                      as loaded_at
from {{ source('bronze', 'dataco_shipments') }}
where _batch_id in (select batch_id from approved_batches)
