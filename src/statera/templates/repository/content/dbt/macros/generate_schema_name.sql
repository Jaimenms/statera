{# Delegates to the framework's medallion convention (statera dbt package). #}
{% macro generate_schema_name(custom_schema_name, node) -%}
    {{ statera.medallion_schema(custom_schema_name, node) }}
{%- endmacro %}
