{#
    Convenção do medalhão: o schema físico é o configurado (bronze, silver, gold, ref), sem o
    prefixo do target. Assim os nomes físicos coincidem com os dos contratos ODCS.
    O projeto do órgão chama esta macro a partir do seu generate_schema_name (o dbt só aceita a
    sobrescrita dessa macro no projeto raiz).
#}
{% macro medallion_schema(custom_schema_name, node) -%}
    {%- if custom_schema_name is none -%}
        {{ target.schema }}
    {%- else -%}
        {{ custom_schema_name | trim }}
    {%- endif -%}
{%- endmacro %}
