{#
    Built in chunks of accounts (macro feat_account_week_sql): the model body
    creates the table from chunk 0, and one post-hook per further chunk
    inserts it, each as its own statement. The unique index exists before the
    inserts, so an account landing in two chunks would fail the build.
#}
{%- set chunks = 8 -%}
{%- set inserts = [] -%}
{%- for chunk in range(1, chunks) -%}
    {%- do inserts.append(
        "insert into {{ this }} {{ feat_account_week_sql(" ~ chunk ~ ", " ~ chunks ~ ") }}"
    ) -%}
{%- endfor -%}

{{ config(
    indexes=[
        {'columns': ['account_id', 'week_end'], 'unique': true},
        {'columns': ['week_end']},
    ],
    pre_hook="set local work_mem = '256MB'",
    post_hook=inserts + ["{{ grant_select_to_api_reader() }}"],
) }}

-- ML features (Phase 3): one row per ACTIVE account-week, that is per account
-- and scoring week (Thursday to Wednesday, macro week_end) with at least one
-- transaction sent or received. Every feature looks back from the end of the
-- week over exact 7-, 30- and 90-day windows (the 7-day window is the week
-- itself), using fct_transactions only, never a transaction dated after the
-- week's end (test feat_account_week_point_in_time). No label is read here.
--
-- Money in = transactions the account received; money out = sent. The
-- counterparty is the other account, and its country is the other side's
-- bank location. Cash = cash_deposit or cash_withdrawal. High-risk country =
-- internal rating high or on a FATF list.
--
-- Columns starting with artefact_ are NOT model features. "The receiver is
-- one of our customers" is an artefact of how SAML-D generates typologies
-- (true for 92.5% of suspicious vs 33.2% of normal transactions), and the
-- customer list covers the whole period, future included. Kept only so its
-- effect can be measured separately.
--
-- Amounts are summed in whole pence (bigint): exact, and PostgreSQL can slide
-- integer sums along a window instead of re-adding the whole window per row.

{{ feat_account_week_sql(0, chunks) }}
