-- Per supplier, versions are numbered 1..n and each one starts exactly where
-- the previous one ends (effective_to is exclusive), with positive length.
with ordered as (
    select
        *,
        row_number() over (partition by supplier_key order by effective_from) as position,
        lag(effective_to) over (partition by supplier_key order by effective_from)
            as previous_effective_to
    from {{ ref('dim_supplier_scd2') }}
)

select *
from ordered
where version_number <> position
   or (position > 1 and effective_from is distinct from previous_effective_to)
   or (effective_to is not null and effective_to <= effective_from)
