-- Recurring query org.example.foo-bar.query.mean-value-by-uf (metadata in query.yaml).
select
    period,
    uf_abbr,
    records,
    mean_value,
    cut_date
from {{ ref('baz_uf_monthly') }}
where uf_abbr = '{{ var("uf", "SP") }}'
order by period
