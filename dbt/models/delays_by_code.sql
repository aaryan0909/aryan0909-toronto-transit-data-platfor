{{ config(materialized='table') }}
-- Gold: top delay codes with their TTC reference descriptions.
select
    code_clean as code,
    coalesce(any_value(code_description), 'Not in reference table') as description,
    count(*) as incidents,
    sum(min_delay) as total_delay_minutes
from {{ source('silver', 'silver_delays') }}
group by code_clean
order by total_delay_minutes desc
limit 25
