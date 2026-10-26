-- Every designation in the raw list appears once in dim_sanctions_designation.
select 'missing or extra designations' as problem
from (select count(distinct unique_id) as n from {{ source('raw', 'uk_sanctions_list') }}) as raw_ids,
     (select count(*) as n from {{ ref('dim_sanctions_designation') }}) as dim_ids
where raw_ids.n <> dim_ids.n
