{#
    Use a model's own schema name as is ("staging", "marts", "evaluation"),
    instead of dbt's default of prefixing it ("dbt_dev_staging").
    In a dev-sample build (--vars '{dev_sample: true}') add "_sample"
    ("marts_sample"), so a sample build can never overwrite the full tables.
#}
{% macro generate_schema_name(custom_schema_name, node) -%}
    {%- set suffix = '_sample' if var('dev_sample') else '' -%}
    {%- if custom_schema_name is none -%}
        {{ target.schema }}{{ suffix }}
    {%- else -%}
        {{ custom_schema_name | trim }}{{ suffix }}
    {%- endif -%}
{%- endmacro %}
