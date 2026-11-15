-- foo records at the cut pinned by the edition, with only what the legacy script reads.

select
    period,
    uf_id,
    cast(value as double) as value
from {{ ref('stg_foo__records') }}
