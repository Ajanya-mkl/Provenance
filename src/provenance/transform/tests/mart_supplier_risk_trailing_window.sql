with eligible_quarters as (
    select
        supplier_key,
        quarter_start,
        row_number() over (
            partition by supplier_key
            order by quarter_start
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

expected_trailing_risk as (
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
    actual.supplier_key,
    actual.quarter
from {{ ref('mart_supplier_risk') }} as actual
inner join expected_trailing_risk as expected
    on expected.supplier_key = actual.supplier_key
    and expected.quarter_start = actual.quarter
where abs(actual.country_risk - expected.country_risk) > 1e-12
   or abs(actual.country_coverage - expected.country_coverage) > 1e-12
