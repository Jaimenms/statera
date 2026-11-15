-- Tabela de divulgação: registros e valor por UF e competência.
--   RN-BAR-001  o registro conta na competência do FATO, não na da recepção

{{ config(meta={'rules': ['RN-BAR-001'], 'gsbpm': '5.7'}) }}

with registros as (

    select *
    from {{ ref('int_bar__registros') }}
    where competencia <= '{{ var("period") }}'

),

ufs as (

    select * from {{ ref('ref_uf') }}

)

select
    registros.competencia,
    ufs.sigla_uf,
    ufs.nome_uf,
    cast('{{ var("cut_date") }}' as date) as data_corte,
    cast(count(*) as bigint) as registros,
    cast(sum(registros.valor) as decimal(14, 2)) as valor_total,
    cast(avg(registros.valor) as decimal(12, 2)) as valor_medio
from registros
inner join ufs on registros.cod_uf = ufs.cod_uf
group by registros.competencia, ufs.sigla_uf, ufs.nome_uf
order by registros.competencia, ufs.sigla_uf
