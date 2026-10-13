-- One row per supplier per eligible quarter. Country risk is destination-based:
-- for each destination, use the latest available LPI at or before the quarter.
with eligible_quarters as (
    select
        supplier_key,
        supplier_name,
        quarter_start,
        late_rate,
        avg_delay_days,
        stddev_delay_days as delay_stddev,
        shipment_count,
        count(*) over (
            partition by supplier_key
            order by quarter_start
            rows between unbounded preceding and current row
        ) as eligible_quarters_to_date
    from {{ ref('int_supplier_quarter_metrics') }}
    where not low_volume
      and not partial_quarter
      and late_rate is not null
),

quarter_lpi as (
    select
        quarters.quarter_start,
        lpi.country_iso3,
        lpi.lpi_score
    from (
        select distinct quarter_start
        from eligible_quarters
    ) as quarters
    inner join {{ ref('stg_worldbank_lpi') }} as lpi
        on lpi.lpi_year <= year(quarters.quarter_start)
    qualify row_number() over (
        partition by quarters.quarter_start, lpi.country_iso3
        order by lpi.lpi_year desc
    ) = 1
),

quarter_lpi_median as (
    select
        quarter_start,
        median(lpi_score) as median_lpi_score
    from quarter_lpi
    group by quarter_start
),

supplier_destination_shipments as (
    select
        eligible.supplier_key,
        eligible.quarter_start,
        eligible.eligible_quarters_to_date,
        shipments.order_country_iso3,
        count(*) as shipment_count
    from eligible_quarters as eligible
    inner join {{ ref('fact_shipments') }} as shipments
        on shipments.supplier_key = eligible.supplier_key
        and shipments.order_date >= eligible.quarter_start
        and shipments.order_date < eligible.quarter_start + interval 3 month
    group by
        eligible.supplier_key,
        eligible.quarter_start,
        eligible.eligible_quarters_to_date,
        shipments.order_country_iso3
),

quarterly_destination_risk as (
    select
        destinations.supplier_key,
        destinations.quarter_start,
        destinations.eligible_quarters_to_date,
        sum(
            destinations.shipment_count
            * (5 - coalesce(lpi.lpi_score, median_lpi.median_lpi_score)) / 4
        ) as weighted_country_risk,
        sum(destinations.shipment_count) as shipment_count,
        sum(destinations.shipment_count) filter (
            where lpi.lpi_score is not null
        ) as lpi_shipment_count
    from supplier_destination_shipments as destinations
    left join quarter_lpi as lpi
        on lpi.quarter_start = destinations.quarter_start
        and lpi.country_iso3 = destinations.order_country_iso3
    inner join quarter_lpi_median as median_lpi
        on median_lpi.quarter_start = destinations.quarter_start
    group by
        destinations.supplier_key,
        destinations.quarter_start,
        destinations.eligible_quarters_to_date
),

trailing_destination_risk as (
    select
        current.supplier_key,
        current.quarter_start,
        sum(history.weighted_country_risk)
        / sum(history.shipment_count) as country_risk,
        sum(history.lpi_shipment_count)::double
        / sum(history.shipment_count) as country_coverage
    from eligible_quarters as current
    inner join quarterly_destination_risk as history
        on history.supplier_key = current.supplier_key
        and history.eligible_quarters_to_date between greatest(
            current.eligible_quarters_to_date
                - {{ var('country_risk_trailing_eligible_quarters', 4) }}
                + 1,
            1
        ) and current.eligible_quarters_to_date
    group by current.supplier_key, current.quarter_start
)

select
    eligible.supplier_key,
    eligible.supplier_name,
    eligible.quarter_start as quarter,
    eligible.late_rate,
    eligible.avg_delay_days,
    eligible.delay_stddev,
    eligible.shipment_count,
    eligible.eligible_quarters_to_date,
    dimension.delivery_band,
    risk.country_risk,
    risk.country_coverage
from eligible_quarters as eligible
inner join {{ ref('dim_supplier_scd2') }} as dimension
    on dimension.supplier_key = eligible.supplier_key
    and dimension.effective_from <= eligible.quarter_start
    and (
        dimension.effective_to > eligible.quarter_start
        or dimension.effective_to is null
    )
inner join trailing_destination_risk as risk
    on risk.supplier_key = eligible.supplier_key
    and risk.quarter_start = eligible.quarter_start