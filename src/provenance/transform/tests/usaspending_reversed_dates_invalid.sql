-- Awards that end before they start are kept but flagged date_valid = false.
select *
from {{ ref('stg_usaspending_awards') }}
where start_date > end_date
  and date_valid
