-- Registros de foo no corte fixado pela edição, só com o que o script legado lê.

select
    competencia,
    cod_uf,
    cast(valor as double) as valor
from {{ ref('stg_foo__registros') }}
