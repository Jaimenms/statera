# RN-BAZ-002 — Mediana do valor por UF (script R legado)

| Campo | Valor |
|---|---|
| Situação | ilustrativa; paridade com o legado em andamento |
| Camada | gold |
| Implementação | `baz_mediana_uf_mensal.R` (R legado), executado pelo Rscript no modelo Python `baz_mediana_uf_mensal` |
| GSBPM | 5.7 Calcular agregados |

## Definição

Mediana do valor dos registros por UF e competência, com duas casas. Com número par de registros,
a média dos dois valores centrais (como a `median` do R).

## Execução

Os casos rodam pela plataforma (`statera rules test`) e **exigem o R**: onde ele não está
instalado, ficam como "não executados"; o CI o instala e os exige.
