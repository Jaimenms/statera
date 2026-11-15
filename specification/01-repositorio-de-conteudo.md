# 01 — Repositório de conteúdo

## Layout do repositório

Um órgão (ou programa estatístico) tem **um repositório de dados**, criado por `statera init <pasta>`:

```
<repositório>/
├── content/        o conteúdo: statera.toml, products/, references/, queries/, recipes/, governance/,
│                   dbt/ e docs/ (documentação gerada). É aqui que se rodam os comandos `statera`
├── publications/   registro das edições publicadas (escrito pelo processo, nunca à mão)
├── docs/           estudos, notas técnicas e decisões que não são código (texto livre)
└── .github/        CODEOWNERS (gerado) e workflows do CI
```

O CI do repositório valida e testa o conteúdo a cada pull request: o `.github/workflows/ci.yml`
gerado pelo `statera init`, ou o fluxo reutilizável da plataforma,
`.github/workflows/validate-repository.yml`.

## `statera.toml`

Na raiz de `content/`. Identifica o órgão e diz à plataforma onde estão as coisas. Os caminhos são
relativos à pasta do `statera.toml`.

```toml
[project]
name = "estat-trabalho"
organization = "Estatísticas do trabalho"  # sem nome de ministério ou de secretaria: eles mudam
acronym = "estat-trabalho"
namespace = "br.gov.estat-trabalho"   # 02-espaco-de-nomes.md
specification = "0.1"
repository_root = ".."               # raiz do repositório, onde fica .github/CODEOWNERS (padrão: a raiz do git)
synthetic = false                    # true marca toda saída como DADOS SINTÉTICOS

[engine]
type = "duckdb"                      # ou postgres, trino...
path = "dbt/data/warehouse.duckdb"

[conventions]                        # nomes das colunas de convenção nos dados do órgão (padrão em inglês)
period_column = "competencia"        # período de referência (padrão: period)
cut_date_column = "data_corte"       # data de corte (padrão: cut_date)
valid_from_column = "vigencia_inicio"  # início da vigência das referências (padrão: valid_from)
valid_to_column = "vigencia_fim"     # fim da vigência (padrão: valid_to)

[dbt]
folder = "dbt"                       # macros/, generated/, dbt_packages/; o dbt_project.yml fica em content/
project = "statera_trabalho"         # nome do projeto dbt (padrão: o name, com _)
profile = "statera_trabalho"         # (padrão: idem)

[registry]
editions = "../publications"         # registro edição a edição (padrão: ../publications)

[openlineage]
namespace = "br.gov.estat-trabalho"

[demo]                               # opcional, só para `statera demo`
editions = [                         # em ordem; cada edição libera antes os cortes que faltam (09)
  { product = "novo-caged", period = "2026-05" },
  { product = "rais-mensalizada", period = "2026-03" },
]
synthetic = "products/esocial-eventos/3-build/synthetic.py:gerar"   # gancho: arquivo.py:função
```

Os nomes das colunas são do conteúdo, no idioma do órgão; a seção `[conventions]` diz à plataforma
quais colunas têm o papel de período de referência, de data de corte e de vigência (09).

A CLI só assume um produto padrão quando o repositório tem um único produto de divulgação; com mais
de um, `--product` é obrigatório (`run`, `resume`, `status`, `evaluate`).

**Nomes.** O nome do repositório e o espaço de nomes **não devem** levar o nome do ministério nem o de
secretaria: a estrutura do governo muda a cada reorganização, e os produtos estatísticos ficam. Um só
repositório guarda o conteúdo (`content/`), o registro de edições (`publications/`) e os estudos e a
documentação que não são código (`docs/`, como o levantamento da situação atual).

## Unidades

Uma **unidade** é um produto ou uma referência. O nome é único no órgão (minúsculas e hífens) e
compõe o identificador (02). Um produto declara, no `product.odps.yaml`, o **alinhamento** (`alignment`) e a
**visibilidade** (`visibility`) (09): de divulgação, ou interno (de fonte ou agregado), lido por outros produtos em
cortes liberados.

| Tipo | Pasta | Reconhecida por | Fases típicas |
|---|---|---|---|
| Produto de divulgação (`alignment: consumer`) | `products/<nome>/` | `1-specify/product.odps.yaml` | 1 a 8, mais `queries/` |
| Produto interno de fonte (`alignment: source`) | `products/<nome>/` | idem | 1, 2, 3, 4 (bronze em lotes), 5 (staging), 7 (cortes) |
| Produto interno agregado (`alignment: aggregate`) | `products/<nome>/` | idem | 1, 2, 5 (silver por corte, regras), 6, 7 (cortes) |
| Referência | `references/<nome>/` | um `*.odcs.yaml` na pasta | — (CSV + contrato com temporalidade, 09) |

O tipo `dominio` das primeiras versões foi substituído pelo produto interno agregado (09): o que vários
produtos compartilham passa a ter dono, contrato de saída, consumidores autorizados e cortes.

Dentro dos produtos, as pastas seguem as fases do GSBPM 5.1: `1-specify`, `2-design`,
`3-build`, `4-collect`, `5-process`, `6-analyse`, `7-disseminate`, `8-evaluate`. Uma unidade só
tem as fases que lhe cabem.

## O que vai em cada fase

| Fase | Arquivo | Esquema |
|---|---|---|
| 1 | `product.odps.yaml`; `classification.yaml`; `owners.yaml`; `calendar.yaml` (produtos de divulgação) | ODPS 1.0 (perfil 04); `classification`, `owners`, `calendar` |
| 2 | `processes/*.yaml` (e o `.bpmn` gerado) | `process` (05) |
| 3 | testes, gerador de dados sintéticos | — |
| 4 | `contracts/*.odcs.yaml` (bronze) | ODCS 3.1 (perfil 04) |
| 5 | `contracts/*.odcs.yaml`, `models/` (dbt: `.sql`, ou `.py` com pandas; `.sps` ou `.R` legado chamado por um `.py`), `rules/<RN>/{rule.md,cases.yaml}`, `macros/`, `metrics.yaml` | ODCS; `rule-cases`; `metrics` |
| 6 | `controls.yaml`, `dbt-tests/*.sql` | `controls` (06) |
| 7 | `series.yaml`, `summary.md.j2` (divulgação); `data/<data>/cut.json`, gerado (internos) | `publication-series` (06); manifesto de corte (09) |
| 8 | `indicators.yaml` | `indicators` (06) |

Transversais: `governance/` (`roles.yaml`, `shared-owners.yaml`, ADRs, `processes/`),
`queries/`, `recipes/` (07), `dbt/` e `docs/` (documentação gerada), todos em `content/`.

O projeto dbt é único e **deve** ter o `dbt_project.yml`, o `packages.yml` (gerado por `statera sync`
com o caminho do pacote instalado, fora do git) e o `profiles.yml` na raiz de `content/` (ao lado do `statera.toml`),
para que os modelos, seeds e testes das unidades fiquem dentro do projeto. `dbt/` guarda só
`macros/`, `generated/` e `dbt_packages/`.

## Modelos em Python e código legado (SPSS e R)

Um modelo do dbt **pode** ser escrito em Python (modelo Python do dbt, com pandas) quando a
transformação fica mais clara assim. O arquivo `<modelo>.py` fica na mesma pasta dos modelos SQL e
**deve** separar a regra numa função pura, `transform(inputs, variables) -> DataFrame`, chamada
por `model(dbt, session)`. Com isso, o modelo Python tem o mesmo tratamento do SQL: contrato, testes,
grupo e acesso, linhagem e execução pelo processo (`dbt:build`). Diferenças:

- os nomes em `dbt.ref()` **devem** ser literais (o dbt os lê do código para montar a linhagem), e
  as variáveis do dbt chegam pela `config` do modelo no `.yml` (`cut_date: "{{ var('cut_date') }}"`);
- o dbt só faz *unit tests* de SQL: os casos das regras de um modelo Python são executados pela
  plataforma, chamando `transform` (`statera rules test`, e o grupo `rules` da validação);
- a regra é citada no `meta` do modelo: `dbt.config(meta={"rules": [...]})`.

**Sintaxe SPSS legada** roda sem reescrita pelo **GNU PSPP**, chamado de um modelo Python
(`statera.legacy.pspp.run`). A única adaptação do `.sps` é trocar os caminhos de arquivo por
FILE HANDLEs (`GET FILE=registros.` … `SAVE OUTFILE=resultado.`), que a plataforma define com as
entradas gravadas em `.sav`. Os casos da regra declaram `requires: [pspp]`: sem o PSPP, ficam não
executados (`not-run`); o CI o instala e os **exige** (`STATERA_EXIGIR=pspp,r`). O PSPP não é o SPSS: antes de o
resultado substituir o do SPSS, a equivalência **deve** ser certificada por script (fundamentos, §10).

**Scripts R legados** rodam da mesma forma, pelo `Rscript --vanilla`, chamado de um modelo Python
(`statera.legacy.r.run`). As entradas vão em `.rds` e a plataforma define, no início do script,
`entradas$<nome>` (caminho de cada entrada) e `saida` (onde gravar o resultado): a adaptação é trocar
os caminhos por `readRDS(entradas$registros)` e `saveRDS(resultado, saida)`. A plataforma não exige
pacotes R; os que o script usa precisam estar instalados no ambiente. Os casos declaram `requires: [r]`.

## Esqueleto do produto

Todo produto **deve** ter os mesmos arquivos, para que quem abre qualquer produto saiba onde procurar
cada coisa. Um arquivo que não se aplica **não falta**: declara `not_applicable: <motivo>`, o que
distingue "não se aplica" de "esqueceram". A conformidade (08) trata um `not_applicable` como ausência
de conteúdo nos níveis que o exigem, e mostra o motivo.

| Arquivo | Divulgação | Interno (fonte ou agregado) |
|---|---|---|
| `1-specify/calendar.yaml` | datas de corte e de divulgação de cada edição | **gerado**: os cortes pedidos pelos calendários dos consumidores, inclusive através de outros produtos internos |
| `2-design/processes/to-be.yaml` | processo to-be (plano de execução) | idem |
| `6-analyse/controls.yaml` | controles entre edições | controles da carga ou do corte, ou `not_applicable` (qualidade nos contratos) |
| `7-disseminate/series.yaml` | série de publicação | **sempre** `not_applicable`: produto interno não é divulgado |
| `8-evaluate/indicators.yaml` | indicadores de qualidade e plano de ação | indicadores de serviço (atraso, completude) |

O modelo de produto (`statera new-product <nome> --alignment consumer|aggregate|source`) cria o
esqueleto com `not_applicable: "a definir: …"` onde ainda não há conteúdo, e o produto nasce com
`conformanceLevel: 0` (rascunho, 08): a validação aceita o esqueleto, e `statera conformance` mostra o
que falta. A validação (grupo `esqueleto`) falha quando falta um arquivo, quando o
motivo está vazio, quando um produto interno tem série e quando o calendário de um produto interno
não é o gerado.

## Dados

Cada fase de cada unidade **deve** guardar o que as execuções produzem em `data/`, fora do controle de
versão. Com a variável `STATERA_DADOS`, todas as pastas `data/` passam para
`$STATERA_DADOS/<products|references>/<unidade>/<fase>/` (testes, ambientes efêmeros). O motor fica onde o
`statera.toml` diz.

## Arquivos gerados

Não se editam à mão; o CI **deve** falhar se estiverem desatualizados:
`dbt_project.yml`, `dbt/generated/*.yml`, o `calendar.yaml` dos produtos internos, `**/2-design/processes/*.bpmn`,
`.github/CODEOWNERS` (na raiz do repositório, `[project] repository_root`). A documentação em
`content/docs/` também é gerada (`statera docs`); `statera sync` regenera os demais.

Cada arquivo gerado **deve** começar com o aviso da plataforma (`statera.gerado`), em comentário
na sintaxe do formato. O aviso diz que o arquivo é gerado, por qual comando, a partir de quais fontes
e como mudar. O repositório de conteúdo **deve** marcar os mesmos arquivos como
`linguist-generated=true` no `.gitattributes`, e **pode** abri-los como somente leitura no editor
(`files.readonlyInclude` no VS Code). Os `.bpmn` ficam fora dessa lista, porque se desenha neles e
depois se importa a mudança para o YAML.
