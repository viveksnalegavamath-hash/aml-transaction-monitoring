-- Guards the decision to treat SAML-D's Amount as GBP for every currency.
-- If amounts were in each currency's own units, the median yen or naira
-- amount would be ~170x or ~600x the GBP median. At decision time (22 Oct
-- 2026) every currency's median was 1.18-1.27x the GBP median.
-- Fails (returns the currency) if any currency's median falls outside half to
-- double the GBP median: the data would then need looking at again.

with medians as (
    select
        payment_currency,
        percentile_cont(0.5) within group (order by amount) as median_amount
    from {{ ref('stg_saml_d') }}
    group by payment_currency
),

gbp as (
    select median_amount as gbp_median from medians where payment_currency = 'GBP'
)

select
    m.payment_currency,
    m.median_amount,
    g.gbp_median,
    m.median_amount / g.gbp_median as ratio_to_gbp
from medians as m
cross join gbp as g
where m.median_amount / g.gbp_median not between 0.5 and 2
