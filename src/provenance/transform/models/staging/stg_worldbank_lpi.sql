-- One row per country per LPI survey year. Rows without a score are dropped,
-- and so are World Bank aggregates (regions, income groups, "World"), which
-- are identified by the wb_country_reference seed (region = 'Aggregates' in
-- the World Bank country API). The join uses the 2-letter code because some
-- aggregate rows have a blank 3-letter code.
with approved_batches as (
    select batch_id
    from {{ source('bronze', 'dq_results') }}
    where source = 'worldbank_lpi'
      and status in ('{{ var("approved_gate_statuses") | join("', '") }}')
)

select
    reference.iso3                  as country_iso3,
    reference.country_name,
    lpi.date::integer               as lpi_year,
    lpi.value::double               as lpi_score,
    lpi.indicator.id                as indicator_id,
    lpi._batch_id                   as batch_id,
    lpi._loaded_at                  as loaded_at
from {{ source('bronze', 'worldbank_lpi') }} as lpi
inner join {{ ref('wb_country_reference') }} as reference
    on lpi.country.id = reference.iso2
where lpi._batch_id in (select batch_id from approved_batches)
  and lpi.value is not null
  and not reference.is_aggregate
