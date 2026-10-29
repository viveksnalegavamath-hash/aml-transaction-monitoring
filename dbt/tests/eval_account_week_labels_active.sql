-- Every labelled (suspicious) account-week is an active account-week in the
-- feature table, so the evaluation can treat every other feature row as
-- negative. Any row returned is a label with no feature row.

select l.account_id, l.week_end
from {{ ref('eval_account_week_labels') }} as l
left join {{ ref('feat_account_week') }} as f using (account_id, week_end)
where f.account_id is null
