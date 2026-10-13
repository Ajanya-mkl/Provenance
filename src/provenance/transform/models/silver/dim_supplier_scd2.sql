-- Supplier SCD Type 2 rebuilt from the quarterly series (gaps-and-islands).
-- dbt snapshots are not used: they record changes at run time and cannot
-- reconstruct past quarters (see docs/architecture.md).
with metrics as (
    select * from {{ ref('int_supplier_quarter_metrics') }}
),

-- Only full-volume, complete quarters are allowed to set new bands.
rated as (
    select
        supplier_key,
        supplier_name,
        quarter_start,
        case
            when low_volume or partial_quarter or late_rate is null then null
            when late_rate < {{ var('delivery_band_low_max') }} then 'low'
            when late_rate >= {{ var('delivery_band_high_min') }} then 'high'
            else 'medium'
        end as delivery_band_observed,
        case
            when low_volume or partial_quarter or late_rate is null then null
            when shipment_count < {{ var('volume_band_low_max') }} then 'low'
            when shipment_count >= {{ var('volume_band_high_min') }} then 'high'
            else 'medium'
        end as volume_band_observed
    from metrics
),

-- Unrated quarters carry the previous bands forward. A supplier whose first
-- quarters are unrated starts as 'unrated' until its first rated quarter.
carried as (
    select
        supplier_key,
        supplier_name,
        quarter_start,
        coalesce(
            last_value(delivery_band_observed ignore nulls) over supplier_history,
            'unrated'
        ) as delivery_band,
        coalesce(
            last_value(volume_band_observed ignore nulls) over supplier_history,
            'unrated'
        ) as volume_band
    from rated
    window supplier_history as (
        partition by supplier_key
        order by quarter_start
        rows between unbounded preceding and current row
    )
),

hashed as (
    select
        *,
        md5(delivery_band || '|' || volume_band) as row_hash
    from carried
),

changes as (
    select
        *,
        case
            when row_hash is distinct from lag(row_hash) over (
                partition by supplier_key order by quarter_start
            ) then 1
            else 0
        end as starts_version
    from hashed
),

islands as (
    select
        *,
        sum(starts_version) over (
            partition by supplier_key
            order by quarter_start
            rows between unbounded preceding and current row
        ) as version_number
    from changes
),

versions as (
    select
        supplier_key,
        any_value(supplier_name)    as supplier_name,
        version_number,
        any_value(delivery_band)    as delivery_band,
        any_value(volume_band)      as volume_band,
        any_value(row_hash)         as row_hash,
        min(quarter_start)          as effective_from
    from islands
    group by supplier_key, version_number
)

select
    supplier_key,
    supplier_name,
    delivery_band,
    volume_band,
    row_hash,
    effective_from,
    lead(effective_from) over (
        partition by supplier_key order by version_number
    )                                                   as effective_to,
    lead(effective_from) over (
        partition by supplier_key order by version_number
    ) is null                                           as is_current,
    version_number::integer                             as version_number
from versions
