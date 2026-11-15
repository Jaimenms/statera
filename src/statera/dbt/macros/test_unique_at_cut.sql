{#
    Uniqueness within the partition of the run's cut, in tables that keep several cuts.
    Use `column_name` (column test) or `columns` (combination, model test).
#}
{% test unique_at_cut(model, column_name=none, columns=none) %}

{%- set chave = columns | join(', ') if columns else column_name -%}

select {{ chave }}, count(*) as repeticoes
from {{ model }}
where {{ var('cut_date_column', 'cut_date') }} = cast('{{ var("cut_date") }}' as date)
group by {{ chave }}
having count(*) > 1

{% endtest %}
