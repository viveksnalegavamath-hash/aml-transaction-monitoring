{#
    Scoring week of the ML model (Phase 3). Weeks run Thursday to Wednesday and
    are named by their last day, the Wednesday, when the week is scored.
    Why Thursday: both evaluation periods end on a Wednesday (31 May 2023 and
    23 Aug 2023) and the hold-out starts on a Thursday (1 Jun 2023), so every
    week lies wholly in one period and the hold-out is exactly 12 weeks.
    Used by the feature table and by the evaluation labels, so both agree.
    isodow: Monday = 1 ... Sunday = 7; Wednesday = 3.
#}
{% macro week_end(date_column) -%}
    ({{ date_column }} + (10 - extract(isodow from {{ date_column }})::int) % 7)
{%- endmacro %}
