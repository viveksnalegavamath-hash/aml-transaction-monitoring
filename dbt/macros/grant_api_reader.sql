{#
    Let the API's read-only role (api.provision, config/api.yaml) read a model
    again after dbt rebuilds it: a rebuild replaces the table, and its grants
    go with the old one. Does nothing if the role has not been created yet, so
    a fresh build works before the API is set up.
#}
{% macro grant_select_to_api_reader() -%}
do $grant$
begin
    if exists (select from pg_roles where rolname = '{{ var("api_reader_role") }}') then
        execute 'grant select on {{ this }} to {{ var("api_reader_role") }}';
    end if;
end
$grant$
{%- endmacro %}
