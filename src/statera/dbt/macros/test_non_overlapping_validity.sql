{#
    Referência com vigência por linha (temporalidade "vigencia", especificação 09): para a mesma
    chave, os intervalos [vigencia_inicio, vigencia_fim] não podem se sobrepor. Gerado a partir do
    contrato da referência.
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
