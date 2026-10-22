{#
    Transaction ID for a raw SAML-D row: MD5 of every column except the label
    (Is_laundering, Laundering_type), so the ID never encodes the answer.
    Checked unique on all 9,504,852 rows. Used by staging and evaluation, so
    the two always produce the same ID for the same row.
#}
{% macro saml_d_transaction_id(alias) -%}
    md5(concat_ws('|',
        {{ alias }}.time, {{ alias }}.date,
        {{ alias }}.sender_account, {{ alias }}.receiver_account,
        {{ alias }}.amount, {{ alias }}.payment_currency, {{ alias }}.received_currency,
        {{ alias }}.sender_bank_location, {{ alias }}.receiver_bank_location,
        {{ alias }}.payment_type
    ))
{%- endmacro %}
