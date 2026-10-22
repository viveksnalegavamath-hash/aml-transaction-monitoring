{#
    Same rule as normalise_name() in fatf_lists.py, which built
    ref.country_name_map.name_key: lower case, single spaces, curly
    apostrophes made straight.
#}
{% macro country_name_key(column) -%}
    lower(regexp_replace(trim(replace({{ column }}, chr(8217), chr(39))), '\s+', ' ', 'g'))
{%- endmacro %}
