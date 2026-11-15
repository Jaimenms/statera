# 04 — Perfis dos padrões

## ODCS v3.1 (contratos)

Além do esquema oficial, todo contrato **deve** ter em `customProperties`:

| Propriedade | Valor |
|---|---|
| `layer` | `bronze` (produto de fonte, fase 4) · `silver` (produto agregado, fase 5) · `gold` (produto de consumo, fase 5) · `ref` (referência). O CI confere com o alinhamento do produto e a fase (09) |
| `urn` | Identificador no espaço de nomes (02), igual ao calculado |
| `classification` | Objeto de quatro dimensões (03); esquema `classification-asset` |
| `dbtSource` / `dbtModel` / `dbtSeed` | Nó do dbt que materializa a tabela |
| `manualProduction` (no lugar do nó dbt) | Como a tabela é produzida hoje, fora do processo (ex.: aba de uma planilha montada à mão). Aceito só até o nível 1 (08); o servidor do contrato aponta para o arquivo publicado (`type: custom`, `location`, `format`) |
| `extraction` (bronze) | `{source, receivedAtColumn, sourceSystem}`: de onde e como a plataforma extrai, em lotes incrementais (09) |
| `partitionedBy` (silver de agregado) | Coluna do corte (a `cut_date_column` de `[conventions]`, ex.: `data_corte`): a tabela guarda um conjunto por corte (09) |
| `temporality` (ref) | `stable` · `validity` · `versioned` (09); `temporalNote` opcional com a política |
| `authority` (ref) | Quem é a autoridade da referência (IBGE, CONCLA, MGI…) |

Colunas: `classification` (compartilhamento: `broad` · `restricted` · `specific`) obrigatória; `tags: [dado-pessoal]` quando for o caso;
`relationships` (ODCS 3.1) para chaves estrangeiras de referências, que viram testes do dbt.
Qualidade: `quality` com métricas da biblioteca do ODCS (`rowCount`, `nullValues`, `invalidValues`…)
ou SQL; verificadas pela plataforma a cada edição.

## ODPS v1.0 (produtos)

`customProperties` do produto: `alignment` (`source` · `aggregate` · `consumer`) e `visibility`
(`internal` · `public`), 09; `authorizedConsumers` (produto interno, 09); `theme`, `frequency`
(`monthly` · `quarterly` · `semiannual` · `annual` · `biweekly` · `on-demand`), `purpose`
(`official-release` · `on-request` · `study` · `dashboard` · `internal-control`), `bases` (os eixos do
edital, nos produtos de consumo), `urn`, `process` (caminho do to-be), `conformanceLevel` (alvo, 08) e
`legacyParity` (`done` · `in-progress` · `not-applicable` · `not-declared`); `sourceSystems` (produto de
fonte); `distributions` (produto de divulgação sem série): a lista dos arquivos publicados hoje, no
vocabulário do DCAT, cada um com `title`, `format`, `url`, `updated` e, opcionalmente, as `ports` que
contém. As `outputPorts` apontam para contratos **da própria unidade** (`customProperties` da porta:
`spreadsheetTab` opcional; `inSpreadsheet: false` deixa a porta fora do xlsx, como os microdados); as `inputPorts`, para contratos de
produtos internos e de referências, e definem as dependências entre produtos (09).

## dbt `exposures` (consumidores e visualizações)

Todo consumidor de tabelas gold é um `exposure`, com `meta.product` e `meta.sharing`. As
visualizações que o público abre declaram também `meta.visualization`:

| Campo | Valor |
|---|---|
| `visualization` | `dashboard` (BI: Power BI, Superset, Metabase…) · `chart` (imagens estáticas no portal) |
| `url` (campo nativo do exposure) | Onde o público abre a visualização |
| `tool` | Ferramenta ou formato |
| `reads` | `published-edition` · `cut-date` · `current` (os três modos de leitura, 07). Uma visualização com `sharing: broad` **deve** ler `published-edition`, para mostrar o mesmo número da planilha |
| `updated` | Quando é atualizada |
| `authors` (opcional) | Crédito a pessoas: `name` e `unit` (07) |
| `charts` (só `chart`) | Para cada imagem: `title`, `port` (a porta de saída com os números do gráfico em tabela) e `alt_text` (e-MAG; Lei 13.146/2015, art. 63; WCAG 1.1.1) |

O catálogo gerado mostra as distribuições e as visualizações de cada produto em "Como acessar", e a
linhagem liga cada visualização ao seu endereço.

## Frictionless Data Package 2.0 (edições e saídas de consulta)

Extensão `x-statera` no pacote: `product`, `urn`, `period`, `cutDate`, `codeVersion`,
`run` (run OpenLineage), `cuts` (corte e execução de cada produto interno lido), `extractions`
(fonte, sistema, data de corte, lote, linhas, hash), `references` (versão, temporalidade, hash; 09), `series`,
`corrects` e `synthetic`. Em cada recurso: `contract`, `urn`, `contractVersion`, `classification`,
`suppressedCells`. Saídas de consulta levam a consulta, a versão, os parâmetros e o hash do SQL.

## OpenLineage 2-0-2

Faceta de execução `statera_edition` com `product`, `period` e `cutDate`; job da edição
(`<produto>.edicao`) como pai dos jobs das tarefas (`<produto>.<Task_…>`) e dos modelos dbt
(`dbt.<modelo>`). Eventos validados contra o esquema oficial antes de gravados.
