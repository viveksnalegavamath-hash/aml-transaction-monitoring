-- UK Sanctions List, one row per source row, cleaned for screening.
-- Name types and alias quality normalised (the source mixes "Primary Name",
-- "Primary name" and "ALias"); full name built from the name parts (name_1-5
-- given names, name_6 surname or entity name); dates of birth parsed into
-- year / month / day where known ("dd/mm/1967" = year only); the list's
-- report date on every row.

with source as (

    select * from {{ source('raw', 'uk_sanctions_list') }}

),

list_date as (

    select to_date(substring(source_note from 'Report Date: (.*)$'), 'DD-Mon-YYYY') as list_date
    from {{ source('raw', 'load_log') }}
    where table_name = 'raw.uk_sanctions_list'
    order by loaded_at desc
    limit 1

),

cleaned as (

    select
        unique_id,
        designation_type,
        case lower(trim(name_type))
            when 'primary name' then 'primary'
            when 'primary name variation' then 'primary_variation'
            when 'alias' then 'alias'
            else 'unknown'
        end                                                     as name_type,
        case lower(trim(alias_strength))
            when 'good quality a.k.a' then 'good'
            when 'low quality a.k.a' then 'low'
        end                                                     as alias_quality,
        nullif(trim(concat_ws(' ', name_1, name_2, name_3, name_4, name_5, name_6)), '')
                                                                as full_name,
        nullif(trim(name_6), '')                                as last_name,
        regime_name                                            as regime_name,
        regexp_replace(coalesce(d_o_b, ''), '[^0-9dmy/]', '', 'gi')
                                                                as dob_text,
        nationality_ies                                         as nationality_text,
        nullif(trim(address_country), '')                       as address_country,
        case when date_designated ~ '^[0-9]{2}/[0-9]{2}/[0-9]{4}$'
             then to_date(date_designated, 'DD/MM/YYYY') end    as date_designated
    from source
    where designation_type in ('Individual', 'Entity', 'Ship')

),

dob as (

    select
        *,
        case when dob_text ~ '[0-9]{4}$'
             then substring(dob_text from '([0-9]{4})$')::int end              as dob_year,
        case when dob_text ~ '^[0-9d]{2}/[0-9]{2}/[0-9]{4}$'
             then split_part(dob_text, '/', 2)::int end                         as dob_month_raw,
        case when dob_text ~ '^[0-9]{2}/[0-9]{2}/[0-9]{4}$'
             then split_part(dob_text, '/', 1)::int end                         as dob_day_raw
    from cleaned

)

select
    d.unique_id,
    d.designation_type,
    d.name_type,
    d.alias_quality,
    d.full_name,
    d.last_name,
    d.regime_name,
    d.dob_year,
    case when d.dob_month_raw between 1 and 12 then d.dob_month_raw end         as dob_month,
    case when d.dob_month_raw between 1 and 12
          and d.dob_day_raw between 1 and extract(
              day from make_date(d.dob_year, d.dob_month_raw, 1) + interval '1 month - 1 day')
         then make_date(d.dob_year, d.dob_month_raw, d.dob_day_raw) end         as dob_date,
    d.nationality_text,
    d.address_country,
    d.date_designated,
    l.list_date
from dob as d
cross join list_date as l
