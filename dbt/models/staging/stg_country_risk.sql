-- One row per country: FATF status and internal WGI rating, with sources.

select
    iso_code,
    country_name,
    fatf_status,
    fatf_measure,
    fatf_list_date,
    fatf_source,
    wgi_control_of_corruption,
    wgi_rule_of_law,
    wgi_regulatory_quality,
    wgi_average,
    internal_rating,
    wgi_data_year,
    wgi_version,
    wgi_source
from {{ source('ref', 'country_risk') }}
