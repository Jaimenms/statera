{#
    Medallion convention: the physical schema is the configured one (bronze, silver, gold, ref), without
    the target prefix, so physical names match those in the ODCS contracts.
    The organization's project calls this macro from its own generate_schema_name (dbt only accepts
    overriding that macro in the root project).
#}
{% macro medallion_schema(custom_schema_name, node) -%}
    {%- if custom_schema_name is none -%}
        {{ target.schema }}
    {%- else -%}
        {{ custom_schema_name | trim }}
    {%- endif -%}
{%- endmacro %}
