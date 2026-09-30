{{ config(materialized='table') }}
-- Gold: station leaderboard (all stations; the dashboard shows the top N).
select
    station,
    count(*) as incidents,
    sum(min_delay) as total_delay_minutes,
    round(avg(case when min_delay > 0 then min_delay end), 2) as avg_delay_minutes_when_delayed
from {{ source('silver', 'silver_delays') }}
where station <> ''
group by station
order by total_delay_minutes desc
