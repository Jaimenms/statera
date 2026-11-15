{#
    Reference table with a validity period per row (temporality "validity", specification 09): for the
    same key, the intervals [valid_from, valid_to] must not overlap. Generated from the reference
    table's contract.
#}
{% test non_overlapping_validity(model, key) %}

select a.{{ key }}, a.{{ var('valid_from_column', 'valid_from') }}, b.{{ var('valid_from_column', 'valid_from') }} as outra_vigencia_inicio
from {{ model }} as a
inner join {{ model }} as b
    on
        a.{{ key }} = b.{{ key }}
        and a.{{ var('valid_from_column', 'valid_from') }} < b.{{ var('valid_from_column', 'valid_from') }}
        and (a.{{ var('valid_to_column', 'valid_to') }} is null or a.{{ var('valid_to_column', 'valid_to') }} >= b.{{ var('valid_from_column', 'valid_from') }})

{% endtest %}
