{#
    Generic test: fails if, in some group (e.g. reference month), a single category of the
    column holds more than `threshold` of the records. It would have caught the real case of the
    tamestabjan field, with 100% of the records in a single band. In tables that keep several cuts, use
    current_cut_only=true to look only at the run's partition.
#}
{% test non_degenerate_distribution(model, column_name, by, threshold=0.9, minimum=50, current_cut_only=false) %}

with contagem as (
    select
        {{ by }} as grupo,
        {{ column_name }} as categoria,
        count(*) as n
    from {{ model }}
    {% if current_cut_only %}where {{ var('cut_date_column', 'cut_date') }} = cast('{{ var("cut_date") }}' as date){% endif %}
    group by 1, 2
),

total as (
    select
        grupo,
        sum(n) as total
    from contagem
    group by 1
)

select
    contagem.grupo,
    contagem.categoria,
    contagem.n,
    total.total,
    round(contagem.n * 1.0 / total.total, 4) as participacao
from contagem
inner join total on contagem.grupo = total.grupo
where
    total.total >= {{ minimum }}
    and contagem.n * 1.0 / total.total > {{ threshold }}

{% endtest %}
