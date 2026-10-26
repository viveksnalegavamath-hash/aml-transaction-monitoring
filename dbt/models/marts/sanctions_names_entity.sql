-- Every distinct name of a sanctioned entity (primary names, variations and
-- aliases), one row per designation and name, with the list date. The
-- screening matches SYNTHETIC KYC businesses against this table.

select distinct
    unique_id,
    full_name,
    name_type,
    alias_quality,
    list_date
from {{ ref('stg_uk_sanctions') }}
where designation_type = 'Entity'
  and full_name is not null
