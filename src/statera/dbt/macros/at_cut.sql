{#
    Read filter for a bronze source AT THE CUT of the run (specification, 09).
    Bronze is incremental: each record is stored once, in the batch it arrived in. A cut is
    "received by the cut-off date" AND "present up to the batch recorded in the cut manifest", so
    that a late batch does not change a cut already marked. The executor passes the batches in var('batches').
#}
{% macro at_cut(received_at_column, table) -%}
    cast({{ received_at_column }} as date) <= cast('{{ var("cut_date") }}' as date)
    and batch <= {{ var("batches", {}).get(table, 2147483647) }}
{%- endmacro %}
