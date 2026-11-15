{#
    Filtro de leitura de uma fonte bronze NO CORTE da execução (especificação, 09).
    O bronze é incremental: cada registro está guardado uma vez, no lote em que chegou. Um corte é
    "recebido até a data de corte" E "presente até o lote registrado no manifesto do corte", para
    que um lote atrasado não mude um corte já marcado. O executor passa os lotes em var('batches').
#}
{% macro at_cut(received_at_column, table) -%}
    cast({{ received_at_column }} as date) <= cast('{{ var("cut_date") }}' as date)
    and batch <= {{ var("batches", {}).get(table, 2147483647) }}
{%- endmacro %}
