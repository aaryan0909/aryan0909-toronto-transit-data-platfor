{{ config(materialized='table') }}
-- Gold: one row per day. delay_minutes counts only incidents with a
-- recorded delay; zero-delay incidents are reported separately so the
-- headline delay number is never quietly diluted.
select
    event_date,
    count(*) as incidents,
    sum(case when min_delay > 0 then 1 else 0 end) as incidents_with_delay,
    sum(min_delay) as total_delay_minutes,
    round(avg(case when min_delay > 0 then min_delay end), 2) as avg_delay_minutes_when_delayed,
    count(distinct station) as stations_affected
from {{ source('silver', 'silver_delays') }}
group by event_date
order by event_date
