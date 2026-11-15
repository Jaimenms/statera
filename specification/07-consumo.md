# 07 — Consumo

Princípio: **o mesmo número, do mesmo jeito, em qualquer ferramenta.** O consumo reaproveita as
definições da produção (contratos, regras, métricas) em vez de cada analista recalculá-las.

## Consultas recorrentes

```
queries/<tema>/<slug>/  ou  products/<p>/queries/<slug>/
├── query.yaml        esquema `query`: id no espaço de nomes, os cinco eixos do edital (theme, product,
│                     bases, frequency, purpose), parâmetros tipados, regras, limitações,
│                     classificação da saída (com controle de divulgação), responsável, situação, versão
├── <slug>.sql        analysis do dbt (nome do arquivo = slug com sublinhados): ref() e var('<parâmetro>', padrão)
└── test.yaml         esquema `query-test`: entradas sintéticas por ref, parâmetros, saída esperada
```

- Tipos de parâmetro: `period`, `date`, `cut`, `ibge-code`, `uf`, `integer`, `short-text`. Valores
  são validados pelo tipo **antes** de chegar ao SQL; parâmetro não declarado é rejeitado.
- Consultas só leem silver, gold e ref (nunca bronze nem staging).
- A saída passa pelo controle de divulgação de `output_classification`; com destino em arquivo, sai
  com um Data Package de procedência.
- Situação (`status`): `exploratory` → `cataloged` → `certified` → `deprecated`.
- `authors` (opcional, `name` e `unit`): crédito a pessoas, além do papel `owner`, inclusive a quem não é da
  equipe de dados. Vai à saída como `contributors` (papel `author`) do Data Package.

## Métricas (`<unidade>/5-process/metrics.yaml`)

Formato inspirado na camada semântica do dbt (MetricFlow): `simple` (`aggregation` de uma
`expression`, com as `sources` possíveis e as `dimensions` que cada fonte permite) e `ratio`
(`numerator` e `denominator`, vezes um `factor`). Agregações: `sum`, `average`, `count`, `min`, `max`. A plataforma escolhe a primeira fonte que cobre as dimensões pedidas.
Filtros vão como parâmetros do motor.

## Receitas (`recipes/`)

Arquivos `.py` em células (`# %%`) ou `.sql`, com cabeçalho `Recipe:`, `Layers:`, `Classification:`.
**Devem** ser executadas no CI contra dados sintéticos.

## Biblioteca

`statera.consume.catalog` (Python, sobre Ibis) com três modos de leitura e procedência em toda saída:

| Modo | Pergunta | Fonte |
|---|---|---|
| vigente | "Qual é o dado mais recente?" | silver e gold no motor |
| data de corte | "Como estava na extração de 20/07?" | partição do bronze; silver/gold só se o motor guardar aquela data |
| edição publicada | "Qual foi o número divulgado?" | arquivo da edição, com o hash do Data Package conferido |

A biblioteca **deve** mostrar a classificação e avisar ao ler o que não é amplo; não é fronteira de
segurança. Uma biblioteca R (dbplyr) seguiria a mesma lógica.

## Contribuição e autoria

O consumo evolui com contribuições de quem não é da equipe de dados (modelo InnerSource):

- **Portas de entrada sem git:** issues "Proposta de nova consulta" e "Contribuir uma análise, um
  cálculo ou um gráfico"; a curadoria as transforma em pull request.
- **Destinos:** consulta catalogada (cálculo recorrente), receita (passo a passo executável) ou
  visualização declarada (`exposure` com `meta.visualization`, 04).
- **Autoria:** `authors` na consulta, `Authors:` no cabeçalho da receita e `meta.authors` na
  visualização, publicados só com consentimento.
- **Catálogo navegável:** `statera docs --interactive` gera o `dbt docs` em página única, com busca, a
  linhagem e o `meta` de cada tabela (produto, contrato, URN, classificação), gerado dos contratos.
