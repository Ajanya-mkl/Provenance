-- Every country code in bronze LPI is in the reference seed, so the aggregate
-- filter in stg_worldbank_lpi never drops or keeps an unknown code silently.
select distinct lpi.country.id as iso2, lpi.country.value as name
from {{ source('bronze', 'worldbank_lpi') }} as lpi
left join {{ ref('wb_country_reference') }} as reference
    on lpi.country.id = reference.iso2
where reference.iso2 is null
