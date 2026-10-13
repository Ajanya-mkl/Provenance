-- One row per USASpending award (key: generated_internal_id; Award ID is not
-- unique). Monthly batches overlap, so the most recently loaded approved batch
-- wins. Rows with impossible dates are kept and flagged, never dropped.
with approved_batches as (
    select batch_id
    from {{ source('bronze', 'dq_results') }}
    where source = 'usaspending_contracts'
      and status in ('{{ var("approved_gate_statuses") | join("', '") }}')
),

typed as (
    select
        generated_internal_id,
        "Award ID"                          as award_id,
        "Recipient Name"                    as recipient_name,
        "Recipient UEI"                     as recipient_uei,
        recipient_id,
        try_cast("Start Date" as date)      as start_date,
        try_cast("End Date" as date)        as end_date,
        "Award Amount"::double              as award_amount,
        "Awarding Agency"                   as awarding_agency,
        "Awarding Sub Agency"               as awarding_sub_agency,
        awarding_agency_id::integer         as awarding_agency_id,
        agency_slug,
        "Contract Award Type"               as contract_award_type,
        "NAICS".code                        as naics_code,
        "NAICS".description                 as naics_description,
        "PSC".code                          as psc_code,
        "PSC".description                   as psc_description,
        _batch_id                           as batch_id,
        _loaded_at                          as loaded_at
    from {{ source('bronze', 'usaspending_contracts') }}
    where _batch_id in (select batch_id from approved_batches)
    qualify row_number() over (
        partition by generated_internal_id
        order by _loaded_at desc, _batch_id desc
    ) = 1
)

select
    *,
    -- Same rules as the Phase 3 gate: end on/after start, start in 1990-2030.
    coalesce(
        start_date <= end_date
        and start_date between date '1990-01-01' and date '2030-12-31',
        false
    ) as date_valid
from typed
