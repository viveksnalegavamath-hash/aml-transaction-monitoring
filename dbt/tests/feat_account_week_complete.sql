-- feat_account_week has exactly one row for every account-week with a
-- transaction (sender or receiver side), and no other rows.
-- Any row returned is an account-week missing from one side.

with expected as (

    select distinct party.account_id, {{ week_end('f.transaction_date') }} as week_end
    from {{ ref('fct_transactions') }} as f
    cross join lateral (values (f.sender_account), (f.receiver_account)) as party (account_id)

)

select
    coalesce(e.account_id, t.account_id)    as account_id,
    coalesce(e.week_end, t.week_end)        as week_end,
    e.account_id is null                    as unexpected_row,
    t.account_id is null                    as missing_row
from expected as e
full join {{ ref('feat_account_week') }} as t using (account_id, week_end)
where e.account_id is null or t.account_id is null
