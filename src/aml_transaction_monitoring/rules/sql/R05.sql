-- R05 High-risk jurisdiction. Spec and thresholds: rules/R05_high_risk_jurisdiction.yaml.
-- Contract: see the header of R01.sql.
--
-- Per payment, so no window: the trigger is the payment itself. Each payment
-- is checked from both sides. The sender is alerted for money going to a
-- risky country (FATF clauses and the internal-rating clause); the receiver
-- for money coming from a country with internal rating high.

with outgoing as (

    select
        transaction_id,
        sender_account                  as account_id,
        transaction_ts,
        amount_gbp,
        'to'                            as direction,
        receiver_country                as country,
        receiver_country_fatf_status = 'call_for_action'
                                        as is_call_for_action,
        receiver_country_fatf_status = 'increased_monitoring'
            and amount_gbp >= %(min_grey_list_gbp)s
                                        as is_grey_list,
        receiver_country_internal_rating = 'high'
            and amount_gbp >= %(min_high_rating_gbp)s
                                        as is_high_rating
    from {fct}
    where receiver_country_fatf_status in ('call_for_action', 'increased_monitoring')
       or receiver_country_internal_rating = 'high'

),

incoming as (

    select
        transaction_id,
        receiver_account                as account_id,
        transaction_ts,
        amount_gbp,
        'from'                          as direction,
        sender_country                  as country,
        false                           as is_call_for_action,  -- FATF clauses: payments sent only
        false                           as is_grey_list,
        amount_gbp >= %(min_high_rating_gbp)s
                                        as is_high_rating
    from {fct}
    where sender_country_internal_rating = 'high'

),

hits as (

    select * from outgoing
    where is_call_for_action or is_grey_list or is_high_rating
    union all
    select * from incoming
    where is_high_rating

)

select
    account_id,
    transaction_ts                  as trigger_ts,
    transaction_ts                  as window_start,
    array[transaction_id]           as transaction_ids,
    jsonb_build_object(
        'amount_gbp', amount_gbp,
        'direction', direction,
        'country', country,
        'matched', concat_ws('; ',
            case when is_call_for_action then 'FATF call for action' end,
            case when is_grey_list then 'FATF increased monitoring' end,
            case when is_high_rating then 'internal rating high' end
        )
    )                               as metrics
from hits
