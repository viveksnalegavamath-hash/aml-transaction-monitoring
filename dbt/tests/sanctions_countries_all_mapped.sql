-- Every nationality and address country on the list (individuals, entities)
-- has an ISO code in the sanctions_country_names seed. A new country name in
-- a future list fails here, rather than silently losing a priority signal.
select distinct trim(part.n) as unmapped
from {{ ref('stg_uk_sanctions') }} as s
cross join lateral unnest(
    array_cat(regexp_split_to_array(coalesce(s.nationality_text, ''), '[,;]'),
              array[coalesce(s.address_country, '')])
) as part (n)
left join {{ ref('sanctions_country_names') }} as c on c.sanctions_country_name = trim(part.n)
where s.designation_type in ('Individual', 'Entity')
  and trim(part.n) <> ''
  and c.iso_code is null
