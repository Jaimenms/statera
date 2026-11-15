{#
    Unicidade dentro da partição do corte da execução, em tabelas que guardam vários cortes.
    Use `column_name` (teste de coluna) ou `columns` (combinação, teste de modelo).
#}
{% test unique_at_cut(model, column_name=none, columns=none) %}

{%- set chave = columns | join(', ') if columns else column_name -%}

select {{ chave }}, count(*) as repeticoes
from {{ model }}
where {{ var('cut_date_column', 'cut_date') }} = cast('{{ var("cut_date") }}' as date)
group by {{ chave }}
having count(*) > 1

{% endtest %}
