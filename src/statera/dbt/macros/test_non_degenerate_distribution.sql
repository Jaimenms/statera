{#
    Teste genérico: falha se, em algum grupo (ex.: competência), uma única categoria da
    coluna concentrar mais que `limite` dos registros. Teria barrado o caso real do campo
    tamestabjan, com 100% dos registros numa só faixa. Em tabelas que guardam vários cortes, use
    so_corte_atual=true para olhar só a partição da execução.
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
