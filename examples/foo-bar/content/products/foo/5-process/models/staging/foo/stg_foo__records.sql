-- Foo System records at the cut pinned by the run, with standardized names and types.

with origem as (

    select *
    from {{ source('foo', 'foo_records') }}
    where {{ statera.at_cut('received_at', 'foo_records') }}

)

select
    record_id,
    person_id,
    cast(uf_id as integer) as uf_id,
    cast(event_date as date) as event_date,
    strftime(cast(event_date as date), '%Y-%m') as period,
    received_at,
    cast(value as decimal(12, 2)) as value,
    batch,
    cast('{{ var("cut_date") }}' as date) as cut_date
from origem
