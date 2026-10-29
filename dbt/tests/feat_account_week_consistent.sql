-- Sanity rules every feat_account_week row must meet. Any row returned breaks
-- one: the week ends on a Wednesday; the account was active that week;
-- windows nest (7 days within 30 within 90); counterparties never exceed
-- payments, new counterparties never exceed counterparties; shares lie in [0, 1].

select account_id, week_end
from {{ ref('feat_account_week') }}
where extract(isodow from week_end) <> 3
   or history_days < 0
   or in_n_7 + out_n_7 < 1
{% for d in ['in', 'out'] %}
   or not ({{ d }}_n_7 <= {{ d }}_n_30 and {{ d }}_n_30 <= {{ d }}_n_90)
   or not ({{ d }}_gbp_7 <= {{ d }}_gbp_30 and {{ d }}_gbp_30 <= {{ d }}_gbp_90)
   or not ({{ d }}_max_gbp_7 <= {{ d }}_max_gbp_30 and {{ d }}_max_gbp_30 <= {{ d }}_max_gbp_90)
   or not ({{ d }}_counterparties_7 <= {{ d }}_counterparties_30
           and {{ d }}_counterparties_30 <= {{ d }}_counterparties_90)
  {% for n in [7, 30, 90] %}
   or {{ d }}_counterparties_{{ n }} > {{ d }}_n_{{ n }}
   or {{ d }}_new_counterparties_{{ n }} > {{ d }}_counterparties_{{ n }}
   or {{ d }}_cash_share_{{ n }} not between 0 and 1.0001
   or {{ d }}_cross_border_share_{{ n }} not between 0 and 1.0001
   or {{ d }}_high_risk_share_{{ n }} not between 0 and 1.0001
  {% endfor %}
{% endfor %}
{% for n in [7, 30, 90] %}
   or out_share_{{ n }} not between 0 and 1.0001
{% endfor %}
