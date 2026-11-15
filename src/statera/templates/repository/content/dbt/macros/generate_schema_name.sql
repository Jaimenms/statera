{# Delegação para a convenção do medalhão da plataforma (pacote dbt statera). #}
{% macro generate_schema_name(custom_schema_name, node) -%}
    {{ statera.medallion_schema(custom_schema_name, node) }}
{%- endmacro %}
