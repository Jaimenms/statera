-- Registros de foo no corte fixado pela edição (o filtro do corte está no staging do produto de fonte).

select
    id_registro,
    cod_uf,
    competencia,
    valor,
    data_corte
from {{ ref('stg_foo__registros') }}
