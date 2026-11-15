-- Recurring query org.example.foo-bar.query.uf-series (metadata in query.yaml).
select
    period,
    uf_abbr,
    records,
    total_value,
    cut_date
from {{ ref('bar_uf_monthly') }}
where uf_abbr = '{{ var("uf", "SP") }}'
order by period
