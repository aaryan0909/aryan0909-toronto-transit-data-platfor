{{ config(materialized='table') }}
-- Gold: incidents and delay minutes by hour of day (0-23).
select
    event_hour,
    count(*) as incidents,
    sum(min_delay) as total_delay_minutes
from {{ source('silver', 'silver_delays') }}
group by event_hour
order by event_hour
