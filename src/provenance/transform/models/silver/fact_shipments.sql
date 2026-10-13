-- Grain: one row per DataCo order item.
select
    items.order_item_id,
    items.order_id,
    items.supplier_key,
    items.order_ts::date                                        as order_date,
    items.shipping_ts::date                                     as shipping_date,
    strftime(items.order_ts, '%Y') || '-Q' || quarter(items.order_ts)
                                                                as order_quarter,
    items.days_real,
    items.days_scheduled,
    items.days_real - items.days_scheduled                      as delay_days,
    items.late_flag,
    items.delivery_status,
    items.shipping_mode,
    country_map.iso3                                            as order_country_iso3,
    items.market,
    items.sales
from {{ ref('stg_dataco_order_items') }} as items
left join {{ ref('country_iso3_map') }} as country_map
    on items.order_country_es = country_map.order_country_es
