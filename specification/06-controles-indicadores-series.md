# 06 — Controles, indicadores e séries

## Controles (`6-analyse/controls.yaml`)

Os controles de identidade da edição corrente são testes SQL do dbt (`6-analyse/dbt-tests/`).
Os que comparam com a edição anterior ou com o histórico são declarados (`controls`, cada um com `id`,
`name`, `type` e os parâmetros do tipo); o **tipo** é da plataforma:

| Tipo | Parâmetros | Resultado |
|---|---|---|
| `revision-between-editions` | `port`, `key`, `measures`, `relative_base`, `warning_threshold`, `error_threshold` | Revisão por chave frente à edição publicada anterior; acima do limite de erro, barra |
| `outlier` | `port`, `measure`, `factor`, `severity` (`warning` · `error`) | Valor da competência frente à média do histórico |

A tabela de revisões do primeiro controle de revisão entra no sumário e no indicador de revisão.

## Indicadores (`8-evaluate/indicators.yaml`)

Cada indicador declara `calculation`, sem código do produto:

- `{function: <nome>, ...}`: função genérica da plataforma (`defasagem_divulgacao`,
  `defasagem_data_corte`, `maior_revisao_relativa` com `control`, `falhas_em_verificacoes`,
  `verificacoes_executadas`, `arquivos_em_formato_aberto`, `duracao_automatica_relativa`,
  `horas_manuais_modeladas`);
- `{sql: ...}`: consulta no motor, com `{{ competencia }}`, `{{ data_corte }}` e
  `{{ publicado('<porta>') }}` (o arquivo publicado da edição).

Alertas (`alert`, com `operator` e `value`) e plano de ação (`action_plan`, GSBPM 8.3) no mesmo arquivo;
a `dimension` de cada indicador é uma das dimensões de qualidade (`relevance`,
`accuracy-and-reliability`, `timeliness-and-punctuality`, `accessibility-and-clarity`,
`coherence-and-comparability`, `efficiency`). A avaliação vai para o
registro de edições junto com a edição aprovada.

## Séries (`7-disseminate/series.yaml`)

Descrevem o que **toda** edição publica: nomes de arquivo (`files_per_edition`, com `{porta}` e
`{competencia}`), `license`, destino no dados.gov.br (`open_data_portal`), `main_resource` (a porta que
resume a edição: o hash dela vai ao índice), políticas de revisão e de retificação
(`revision_policy`, `correction_policy`) e o caminho no registro de edições (`edition_registry`). O
sumário é um modelo Jinja2 (`summary.md.j2`) que recebe as tabelas publicadas (`tables[<porta>]`), a
edição (`edition`), o produto (`product`), as revisões (`revisions`) e os controles de divulgação
(`controls`), com filtros pt-BR (`milhar`, `sinal`, `pct`, `reais`) e `in_period` (as linhas da
competência da edição).
