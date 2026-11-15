-- foo records at the cut pinned by the edition (the cut filter is in the source product's staging).

select
    record_id,
    uf_id,
    period,
    value,
    cut_date
from {{ ref('stg_foo__records') }}
