{#
    Use a model's own schema name as is ("staging", "marts", "evaluation"),
    instead of dbt's default of prefixing it ("dbt_dev_staging").
#}
{% macro generate_schema_name(custom_schema_name, node) -%}
    {%- if custom_schema_name is none -%}
        {{ target.schema }}
    {%- else -%}
        {{ custom_schema_name | trim }}
    {%- endif -%}
{%- endmacro %}
