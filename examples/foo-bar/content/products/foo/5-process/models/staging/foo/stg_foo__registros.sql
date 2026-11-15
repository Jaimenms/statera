-- Registros do Sistema Foo no corte fixado pela execução, com nomes e tipos padronizados.

with origem as (

    select *
    from {{ source('foo', 'foo_registros') }}
    where {{ statera.at_cut('data_recepcao', 'foo_registros') }}

)

select
    id_registro,
    id_pessoa,
    cast(cod_uf as integer) as cod_uf,
    cast(data_fato as date) as data_fato,
    strftime(cast(data_fato as date), '%Y-%m') as competencia,
    data_recepcao,
    cast(valor as decimal(12, 2)) as valor,
    batch,
    cast('{{ var("cut_date") }}' as date) as data_corte
from origem
