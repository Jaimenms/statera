-- Consulta recorrente br.gov.foo-bar.query.valor-medio-uf (metadados em query.yaml).
select
    competencia,
    sigla_uf,
    registros,
    valor_medio,
    data_corte
from {{ ref('baz_uf_mensal') }}
where sigla_uf = '{{ var("uf", "SP") }}'
order by competencia
