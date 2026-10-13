-- Shipment-grain drill-down mart. No customer-identifying fields are exposed.
select
    shipments.order_item_id as shipment_id,
    shipments.supplier_key,
    metrics.supplier_name,
    shipments.order_quarter as quarter,
    shipments.delay_days,
    shipments.late_flag,
    shipments.shipping_mode,
    shipments.order_country_iso3 as country
from {{ ref('fact_shipments') }} as shipments
inner join {{ ref('int_supplier_quarter_metrics') }} as metrics
    on metrics.supplier_key = shipments.supplier_key
    and metrics.quarter_start = date_trunc('quarter', shipments.order_date)::date