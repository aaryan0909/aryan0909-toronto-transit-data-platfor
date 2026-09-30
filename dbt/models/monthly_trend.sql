{{ config(materialized='table') }}
-- Gold: monthly trend for the dashboard time series.
select
    event_year_month,
    count(*) as incidents,
    sum(min_delay) as total_delay_minutes,
    sum(case when min_delay > 0 then 1 else 0 end) as incidents_with_delay
from {{ source('silver', 'silver_delays') }}
group by event_year_month
order by event_year_month
