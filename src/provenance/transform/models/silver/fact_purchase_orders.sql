-- Grain: one row per DataCo order. Order date and country are constant
-- within an order (verified in bronze), so any_value is exact.
select
    items.order_id,
    any_value(items.order_ts)::date                                     as order_date,
    any_value(strftime(items.order_ts, '%Y') || '-Q' || quarter(items.order_ts))
                                                                        as order_quarter,
    count(*)                                                            as item_count,
    sum(items.item_total)                                               as order_total,
    any_value(country_map.iso3)                                         as order_country_iso3
from {{ ref('stg_dataco_order_items') }} as items
left join {{ ref('country_iso3_map') }} as country_map
    on items.order_country_es = country_map.order_country_es
group by items.order_id
