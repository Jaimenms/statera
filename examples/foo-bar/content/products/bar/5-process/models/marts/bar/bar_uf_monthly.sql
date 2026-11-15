-- Release table: records and value by UF and reference month.
--   BR-BAR-001  a record counts in the reference month of the EVENT, not of reception

{{ config(meta={'rules': ['BR-BAR-001'], 'gsbpm': '5.7'}) }}

with records as (

    select *
    from {{ ref('int_bar__records') }}
    where period <= '{{ var("period") }}'

),

ufs as (

    select * from {{ ref('ref_uf') }}

)

select
    records.period,
    ufs.uf_abbr,
    ufs.uf_name,
    cast('{{ var("cut_date") }}' as date) as cut_date,
    cast(count(*) as bigint) as records,
    cast(sum(records.value) as decimal(14, 2)) as total_value,
    cast(avg(records.value) as decimal(12, 2)) as mean_value
from records
inner join ufs on records.uf_id = ufs.uf_id
group by records.period, ufs.uf_abbr, ufs.uf_name
order by records.period, ufs.uf_abbr
