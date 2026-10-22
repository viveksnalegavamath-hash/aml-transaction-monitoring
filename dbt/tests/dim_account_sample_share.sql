-- In a full build, about dev_sample_percent of accounts are flagged
-- in_dev_sample (the md5 split should be even). Fails outside +/- 1 point.
-- Not checked in a sample build (there, most counterparties are unsampled),
-- but the ref stays outside the if, so dbt links the test in both builds.

select share
from (
    select 100.0 * avg(in_dev_sample::int) as share
    from {{ ref('dim_account') }}
) as s
{% if var('dev_sample') %}
where false
{% else %}
where share not between {{ var('dev_sample_percent') }} - 1 and {{ var('dev_sample_percent') }} + 1
{% endif %}
