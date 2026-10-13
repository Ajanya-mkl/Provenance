-- Supplier SCD Type 2 rebuilt from the quarterly series (gaps-and-islands).
-- dbt snapshots are not used: they record changes at run time and cannot
-- reconstruct past quarters (see docs/architecture.md).
with recursive metrics as (
    select
        *,
        row_number() over (
            partition by supplier_key order by quarter_start
        ) as quarter_number
    from {{ ref('int_supplier_quarter_metrics') }}
),

-- Only full-volume, complete quarters are allowed to set new bands.
rated as (
    select
        supplier_key,
        supplier_name,
        quarter_start,
        quarter_number,
        not low_volume and not partial_quarter and late_rate is not null as eligible,
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

carried as (
    select
        *,
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

-- Track the current delivery band and pending candidate across eligible
-- quarters. Ineligible quarters preserve, but do not advance, a candidate.
band_state (
    supplier_key,
    supplier_name,
    quarter_start,
    quarter_number,
    delivery_band_observed,
    volume_band,
    delivery_band,
    pending_band,
    pending_count,
    pending_from,
    event_delivery_band,
    event_from,
    version_number,
    is_event
) as (
    select
        supplier_key,
        supplier_name,
        quarter_start,
        quarter_number,
        delivery_band_observed,
        volume_band,
        coalesce(delivery_band_observed, 'unrated') as delivery_band,
        null::varchar as pending_band,
        0::integer as pending_count,
        null::date as pending_from,
        coalesce(delivery_band_observed, 'unrated') as event_delivery_band,
        quarter_start as event_from,
        1::integer as version_number,
        true as is_event
    from carried
    where quarter_number = 1

    union all

    select
        next_quarter.supplier_key,
        next_quarter.supplier_name,
        next_quarter.quarter_start,
        next_quarter.quarter_number,
        next_quarter.delivery_band_observed,
        next_quarter.volume_band,
        case
            when confirmation.confirms then next_quarter.delivery_band_observed
            else prior.delivery_band
        end as delivery_band,
        case
            when not next_quarter.eligible then prior.pending_band
            when next_quarter.delivery_band_observed = prior.delivery_band then null
            when confirmation.confirms then null
            else next_quarter.delivery_band_observed
        end as pending_band,
        case
            when not next_quarter.eligible then prior.pending_count
            when next_quarter.delivery_band_observed = prior.delivery_band then 0
            when confirmation.confirms then 0
            else pending.next_pending_count
        end as pending_count,
        case
            when not next_quarter.eligible then prior.pending_from
            when next_quarter.delivery_band_observed = prior.delivery_band then null
            when confirmation.confirms then null
            else pending.next_pending_from
        end as pending_from,
        case
            when confirmation.confirms then next_quarter.delivery_band_observed
            else null
        end as event_delivery_band,
        case
            when confirmation.confirms then pending.next_pending_from
            else null
        end as event_from,
        prior.version_number + confirmation.confirms::integer as version_number,
        confirmation.confirms as is_event
    from band_state as prior
    inner join carried as next_quarter
        on next_quarter.supplier_key = prior.supplier_key
        and next_quarter.quarter_number = prior.quarter_number + 1
    cross join lateral (
        select
            case
                when next_quarter.delivery_band_observed = prior.pending_band
                    then prior.pending_count + 1
                else 1
            end as next_pending_count,
            case
                when next_quarter.delivery_band_observed = prior.pending_band
                    then prior.pending_from
                else next_quarter.quarter_start
            end as next_pending_from
    ) as pending
    cross join lateral (
        select
            next_quarter.eligible
            and next_quarter.delivery_band_observed is distinct from prior.delivery_band
            and pending.next_pending_count >= {{ var('band_change_confirming_quarters') }}
            as confirms
    ) as confirmation
),

events as (
    select
        supplier_key,
        event_delivery_band as delivery_band,
        event_from as effective_from,
        version_number
    from band_state
    where is_event
),

versions as (
    select
        events.supplier_key,
        carried.supplier_name,
        events.delivery_band,
        carried.volume_band,
        md5(events.delivery_band) as row_hash,
        events.effective_from,
        events.version_number
    from events
    inner join carried
        on carried.supplier_key = events.supplier_key
        and carried.quarter_start = events.effective_from
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
