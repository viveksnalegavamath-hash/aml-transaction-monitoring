-- Every transaction's label agrees with its typology's suspicious flag in the
-- seed (e.g. no "normal_fan_out" row marked suspicious). Returns disagreements.

select transaction_id, is_laundering, typology, is_suspicious_typology
from {{ ref('eval_transaction_labels') }}
where is_laundering <> is_suspicious_typology
