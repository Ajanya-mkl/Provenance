-- Every supplier has exactly one current version.
select supplier_key, count(*) filter (where is_current) as current_rows
from {{ ref('dim_supplier_scd2') }}
group by supplier_key
having count(*) filter (where is_current) <> 1
