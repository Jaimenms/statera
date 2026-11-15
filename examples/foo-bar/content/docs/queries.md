# Consultas recorrentes

> ARQUIVO GERADO pela plataforma Statera. Não edite à mão: a próxima geração desfaz a mudança.  
> Gerado por `statera docs`. Fontes: metadados do repositório (contratos, produtos, processos, regras, séries).  
> Para mudar: edite as fontes e rode `statera docs`.  
> **Exemplo com DADOS SINTÉTICOS.**

Consultas que se repetem, padronizadas, parametrizadas, testadas e organizadas pelos cinco eixos do edital. Cada uma é uma *analysis* do dbt (aparece na linhagem) com metadados em `query.yaml`. Executar: `statera query run <id> --param nome=valor`.

| Consulta | Tema | Produto | Bases | Periodicidade | Finalidade | Situação |
|---|---|---|---|---|---|---|
| [Série de registros e valor de uma UF](../products/bar/queries/serie-uf/query.yaml) | foo | bar | gold.bar_uf_mensal | on-demand | on-request | cataloged |
| [Série do valor médio de uma UF](../products/baz/queries/valor-medio-uf/query.yaml) | foo | baz | gold.baz_uf_mensal | on-demand | on-request | cataloged |

## Série de registros e valor de uma UF

`br.gov.foo-bar.query.serie-uf` · versão 0.1.0 · responsável `equipe-bar` · [SQL](../products/bar/queries/serie-uf/serie_uf.sql)

Série mensal de registros e valor total de uma UF, da tabela de divulgação.

| Parâmetro | Tipo | Obrigatório | Padrão | Descrição |
|---|---|---|---|---|
| `uf` | uf | sim |  | Sigla da UF |

- **Regras aplicadas:** RN-BAR-001
- **Limitações:** Números do último corte processado; competências recentes ainda mudam.
- **Classificação da saída:** broad · aggregated · none

## Série do valor médio de uma UF

`br.gov.foo-bar.query.valor-medio-uf` · versão 0.1.0 · responsável `equipe-bar` · [SQL](../products/baz/queries/valor-medio-uf/valor_medio_uf.sql)

Série mensal do valor médio de uma UF, da tabela de divulgação de baz.

| Parâmetro | Tipo | Obrigatório | Padrão | Descrição |
|---|---|---|---|---|
| `uf` | uf | sim |  | Sigla da UF |

- **Regras aplicadas:** RN-BAZ-001
- **Limitações:** Calculado pelo script SPSS legado; competências recentes ainda mudam.
- **Classificação da saída:** broad · aggregated · none
