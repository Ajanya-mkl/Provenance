-- One row per supplier per calendar quarter in which it had shipments.
-- Late rate and delay exclude cancelled shipments (their delivery never
-- completed), but cancelled shipments still count toward shipment_count.
with items as (
    select
        *,
        date_trunc('quarter', order_ts)::date as quarter_start
    from {{ ref('stg_dataco_order_items') }}
),

data_bounds as (
    select
        min(order_ts)::date as first_order_date,
        max(order_ts)::date as last_order_date
    from items
),

quarters as (
    select
        quarter_start,
        (quarter_start + interval 3 month - interval 1 day)::date as quarter_end,
        count(distinct order_ts::date)                         as days_with_orders
    from items
    group by quarter_start
),

-- A quarter is partial when the data starts after it begins or ends before
-- it finishes, or when too few of its days have any order at all.
quarter_flags as (
    select
        quarters.quarter_start,
        quarters.days_with_orders::double
            / (date_diff('day', quarters.quarter_start, quarters.quarter_end) + 1)
            as day_coverage,
        (
            data_bounds.first_order_date > quarters.quarter_start
            or data_bounds.last_order_date < quarters.quarter_end
            or quarters.days_with_orders::double
                / (date_diff('day', quarters.quarter_start, quarters.quarter_end) + 1)
                < {{ var('partial_quarter_min_day_coverage') }}
        ) as partial_quarter
    from quarters
    cross join data_bounds
),

supplier_quarters as (
    select
        supplier_key,
        any_value(category_name)                                    as supplier_name,
        quarter_start,
        count(*)                                                    as shipment_count,
        count(*) filter (where canceled_flag)                       as canceled_count,
        count(*) filter (where not canceled_flag and late_flag)     as late_count,
        avg(days_real - days_scheduled) filter (where not canceled_flag)
                                                                    as avg_delay_days,
        stddev_samp(days_real - days_scheduled) filter (where not canceled_flag)
                                                                    as stddev_delay_days
    from items
    group by supplier_key, quarter_start
)

select
    supplier_quarters.supplier_key,
    supplier_quarters.supplier_name,
    supplier_quarters.quarter_start,
    strftime(supplier_quarters.quarter_start, '%Y') || '-Q'
        || quarter(supplier_quarters.quarter_start)                as order_quarter,
    supplier_quarters.shipment_count,
    supplier_quarters.canceled_count,
    supplier_quarters.late_count,
    supplier_quarters.late_count::double
        / nullif(supplier_quarters.shipment_count - supplier_quarters.canceled_count, 0)
                                                                    as late_rate,
    supplier_quarters.avg_delay_days,
    supplier_quarters.stddev_delay_days,
    supplier_quarters.shipment_count < {{ var('min_quarter_shipments') }}
                                                                    as low_volume,
    quarter_flags.partial_quarter,
    quarter_flags.day_coverage
from supplier_quarters
inner join quarter_flags using (quarter_start)
