-- A new version exists only when the tracked attributes changed.
with ordered as (
    select
        *,
        lag(row_hash) over (partition by supplier_key order by version_number)
            as previous_row_hash
    from {{ ref('dim_supplier_scd2') }}
)

select *
from ordered
where row_hash = previous_row_hash
