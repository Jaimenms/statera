# RN-BAZ-001 — Valor médio por UF (script SPSS legado)

| Campo | Valor |
|---|---|
| Situação | ilustrativa; paridade com o SPSS em andamento |
| Camada | gold |
| Implementação | `baz_uf_mensal.sps` (SPSS legado), executado pelo GNU PSPP no modelo Python `baz_uf_mensal` |
| GSBPM | 5.7 Calcular agregados |

## Definição

Valor médio = valor total / número de registros, por UF e competência, com duas casas.

## Execução

Os casos rodam pela plataforma (`statera rules test`) e **exigem o GNU PSPP**: onde ele não está
instalado, ficam como "não executados"; o CI o instala e os exige. O PSPP não é o SPSS: antes de o
resultado substituir o do SPSS, a equivalência é certificada (o mesmo corte nos dois, comparação
célula a célula).
