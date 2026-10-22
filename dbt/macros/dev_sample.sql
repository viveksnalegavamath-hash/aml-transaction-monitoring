{#
    Dev sample: "10% account sample (≈19% of rows)". FOR DEVELOPMENT SPEED ONLY;
    every reported result comes from the full data.

    An account is sampled when the first 8 hex digits of md5(account ID), read
    as a number, mod 100, are below var('dev_sample_percent'). Deterministic,
    no random(), and it only ever sees the account ID (never the label).
    The same rule in Python: int(hashlib.md5(id.encode()).hexdigest()[:8], 16) % 100 < 10
    md5 rather than PostgreSQL's hashtext(), which is internal and may change
    between versions.
#}
{% macro in_dev_sample(account_column) -%}
    (mod(('x' || left(md5({{ account_column }}), 8))::bit(32)::bigint, 100)
        < {{ var('dev_sample_percent') }})
{%- endmacro %}

{#
    WHERE clause keeping every transaction whose sender OR receiver is sampled,
    over the full date range. Empty unless the build runs with
    --vars '{dev_sample: true}', so a normal build keeps all rows.
#}
{% macro dev_sample_filter(sender_column, receiver_column) -%}
    {%- if var('dev_sample') -%}
    where {{ in_dev_sample(sender_column) }} or {{ in_dev_sample(receiver_column) }}
    {%- endif -%}
{%- endmacro %}
