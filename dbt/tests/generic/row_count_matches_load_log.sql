{#
    Passes when the model has exactly as many rows as the latest load of
    `table_name` recorded in raw.load_log. Fails (returns one row showing
    both counts) if rows were lost or duplicated between raw and the model.

    In a dev-sample build, SAML-D models are compared with raw.saml_d filtered
    by the same sample rule instead, so the test still catches lost rows.
#}
{% test row_count_matches_load_log(model, table_name) %}

with model_count as (
    select count(*) as model_rows from {{ model }}
),

{% if var('dev_sample') and table_name == 'raw.saml_d' %}
logged as (
    select count(*) as logged_rows
    from {{ source('raw', 'saml_d') }}
    {{ dev_sample_filter('sender_account', 'receiver_account') }}
)
{% else %}
logged as (
    select row_count as logged_rows
    from {{ source('raw', 'load_log') }}
    where table_name = '{{ table_name }}'
    order by load_id desc
    limit 1
)
{% endif %}

select model_rows, logged_rows
from model_count
left join logged on true
where logged_rows is null or model_rows <> logged_rows

{% endtest %}
