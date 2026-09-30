{{ config(materialized='table') }}
-- Gold: delay burden by canonical line.
select
    line_canonical,
    count(*) as incidents,
    sum(min_delay) as total_delay_minutes,
    round(avg(case when min_delay > 0 then min_delay end), 2) as avg_delay_minutes_when_delayed,
    round(100.0 * sum(case when min_delay > 0 then 1 else 0 end) / count(*), 1) as pct_incidents_with_delay
from {{ source('silver', 'silver_delays') }}
group by line_canonical
order by total_delay_minutes desc
