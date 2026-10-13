-- effective_to is null exactly when the version is current.
select *
from {{ ref('dim_supplier_scd2') }}
where (effective_to is null) <> is_current
