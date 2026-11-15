# RN-BAR-002 — Variação sobre o mês anterior

| Campo | Valor |
|---|---|
| Situação | ilustrativa |
| Camada | gold |
| Implementação | `bar_variacao_uf_mensal` (modelo **Python** do dbt, pandas) |
| GSBPM | 5.7 Calcular agregados |

## Definição

A variação de uma UF numa competência é `(registros / registros do mês anterior − 1) × 100`, com duas
casas. Na primeira competência da UF, não há mês anterior: a variação fica vazia, e não zero.

Os casos desta regra rodam pela plataforma (`statera rules test`), chamando a função pura do
modelo; o dbt só faz *unit tests* de modelos SQL.
