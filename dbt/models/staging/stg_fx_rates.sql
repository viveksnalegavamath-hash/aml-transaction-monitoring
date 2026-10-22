-- One HMRC rate per currency per month (currency units per £1).
-- HMRC lists some currencies under several countries (AED: Abu Dhabi, Dubai,
-- UAE). DISTINCT keeps one row when their rates agree; if they ever disagreed,
-- two rows would remain and the uniqueness test on (currency, month) fails.

select distinct
    currency_code,
    start_date      as month_start,
    end_date        as month_end,
    units_per_gbp
from {{ source('raw', 'fx_rates_hmrc') }}
