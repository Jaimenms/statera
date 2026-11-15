-- Consulta recorrente br.gov.foo-bar.query.serie-uf (metadados em query.yaml).
select
    competencia,
    sigla_uf,
    registros,
    valor_total,
    data_corte
from {{ ref('bar_uf_mensal') }}
where sigla_uf = '{{ var("uf", "SP") }}'
order by competencia
