-- One row per UK Sanctions List designation (individual, entity or ship),
-- with every known date of birth, nationality and address country (as ISO
-- codes, via the sanctions_country_names seed), regimes and the list date.
-- Real public data (FCDO, OGL v3.0).

with names as (

    select * from {{ ref('stg_uk_sanctions') }}

),

countries as (

    select * from {{ ref('sanctions_country_names') }}

),

nationalities as (

    select distinct n.unique_id, c.iso_code
    from names as n
    cross join lateral unnest(regexp_split_to_array(n.nationality_text, '[,;]')) as part (nationality)
    join countries as c on c.sanctions_country_name = trim(part.nationality)

),

address_countries as (

    select distinct n.unique_id, c.iso_code
    from names as n
    join countries as c on c.sanctions_country_name = n.address_country

)

select
    n.unique_id,
    min(n.designation_type)                                                 as designation_type,
    min(n.full_name) filter (where n.name_type = 'primary')                as primary_name,
    count(distinct n.full_name)                                             as n_names,
    string_agg(distinct n.regime_name, '; ')                                as regimes,
    min(n.date_designated)                                                  as date_designated,
    array_remove(array_agg(distinct n.dob_date), null)                      as dob_dates,
    array_remove(array_agg(distinct n.dob_year), null)                      as dob_years,
    (select array_agg(iso_code order by iso_code) from nationalities as x
      where x.unique_id = n.unique_id)                                      as nationality_isos,
    (select array_agg(iso_code order by iso_code) from address_countries as x
      where x.unique_id = n.unique_id)                                      as address_country_isos,
    min(n.list_date)                                                        as list_date
from names as n
group by n.unique_id
