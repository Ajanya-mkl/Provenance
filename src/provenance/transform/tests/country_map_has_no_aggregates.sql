-- No DataCo country maps to a World Bank aggregate (region, income group...).
select country_map.*
from {{ ref('country_iso3_map') }} as country_map
inner join {{ ref('wb_country_reference') }} as reference
    on country_map.iso3 = reference.iso3
where reference.is_aggregate
