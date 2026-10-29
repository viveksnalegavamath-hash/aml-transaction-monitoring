{{ config(indexes=[{'columns': ['account_id', 'week_end'], 'unique': true}]) }}

-- The ML answer key: one row per account-week with at least one suspicious
-- transaction sent or received (positives only; every other active
-- account-week in features.feat_account_week is negative).
-- EVALUATION ONLY. Joined to the features only by the evaluation code.
-- Same week_end macro as the feature table, so the weeks match.

with suspicious as (

    select f.transaction_id, f.transaction_date, f.sender_account, f.receiver_account, e.typology
    from {{ ref('eval_transaction_labels') }} as e
    join {{ ref('fct_transactions') }} as f using (transaction_id)
    where e.is_laundering

)

select
    party.account_id,
    {{ week_end('s.transaction_date') }}            as week_end,
    count(distinct s.transaction_id)                as suspicious_transactions,
    array_agg(distinct s.typology order by s.typology)  as typologies
from suspicious as s
cross join lateral (values (s.sender_account), (s.receiver_account)) as party (account_id)
group by party.account_id, {{ week_end('s.transaction_date') }}
