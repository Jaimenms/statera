# Séries de publicação

> ARQUIVO GERADO pela plataforma Statera. Não edite à mão: a próxima geração desfaz a mudança.  
> Gerado por `statera docs`. Fontes: metadados do repositório (contratos, produtos, processos, regras, séries).  
> Para mudar: edite as fontes e rode `statera docs`.  
> **Exemplo com DADOS SINTÉTICOS.**

Cada série descreve o que **toda** edição publica. As edições são registradas pelo processo, fora deste repositório (`../publications`, em statera.toml), para que publicar não exija commit no código.

## Bar — registros e valor por UF (DADOS SINTÉTICOS)

Fonte: [series.yaml](../products/bar/7-disseminate/series.yaml) · modelo do sumário: [summary.md.j2](../products/bar/7-disseminate/summary.md.j2)

| Item | Valor |
|---|---|
| Produto | [Bar — registros e valor por UF](../products/bar/1-specify/product.odps.yaml) |
| Periodicidade | monthly |
| Início | 2026-01 |
| Licença | Creative Commons Attribution 4.0 (ilustrativo) |
| dados.gov.br |  |
| Arquivos por edição | por-uf.csv, variacao-uf.csv, bar-{competencia}.xlsx, summary.md, datapackage.json, ckan.json |
| Porta principal | por-uf |
| Registro de cada edição | `bar/{competencia}.datapackage.json` |
| Índice da série | `bar/editions.csv` |
| Quem escreve o registro | processo to-be (tarefa Task_Publicar) |

**Cobertura.** Cada edição republica o ano corrente até a competência da edição, no corte da edição.

**Revisão.** Competências já divulgadas são revistas a cada edição; a edição anterior não é alterada.

**Retificação.** Uma edição publicada não é recalculada; retificar exige `statera run --force`.

## Baz — valor médio por UF (script SPSS legado; DADOS SINTÉTICOS)

Fonte: [series.yaml](../products/baz/7-disseminate/series.yaml) · modelo do sumário: [summary.md.j2](../products/baz/7-disseminate/summary.md.j2)

| Item | Valor |
|---|---|
| Produto | [Baz — valor médio e mediana por UF (scripts SPSS e R legados)](../products/baz/1-specify/product.odps.yaml) |
| Periodicidade | monthly |
| Início | 2026-01 |
| Licença | Creative Commons Attribution 4.0 (ilustrativo) |
| dados.gov.br |  |
| Arquivos por edição | valor-medio-uf.csv, mediana-uf.csv, baz-{competencia}.xlsx, summary.md, datapackage.json, ckan.json |
| Porta principal | valor-medio-uf |
| Registro de cada edição | `baz/{competencia}.datapackage.json` |
| Índice da série | `baz/editions.csv` |
| Quem escreve o registro | processo to-be (tarefa Task_Publicar), quando houver |

**Cobertura.** Cada edição republica o ano corrente até a competência da edição, no corte da edição.

**Revisão.** Competências já divulgadas são revistas a cada edição; a edição anterior não é alterada.

**Retificação.** Uma edição publicada não é recalculada; retificar exige `statera run --force`.
