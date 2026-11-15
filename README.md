# Statera

**Statera** é uma plataforma para repositórios de produtos estatísticos de governo: dados, regras,
processos e publicações organizados como produtos de dados, com contratos, classificação da
informação, processo executável, controle de divulgação e registro de cada edição publicada.

> *Statera* é a balança romana de braço: um instrumento de medida com peso aferido e público.

A plataforma é **genérica**: não conhece nenhum produto. Tudo o que é de um órgão (produtos,
referências, consultas, receitas, governança) fica num **repositório de dados** do órgão, que declara
a Statera como dependência. Uma melhoria na plataforma chega a todos por atualização de versão.

```
ESPECIFICAÇÃO   specification/               formatos, perfis, espaço de nomes, classificação, níveis, cortes
     │ implementada por
PLATAFORMA      src/statera/                 pacote Python + CLI · pacote dbt · fluxo de CI · modelos (Copier)
     │ usada por
REPOSITÓRIO     <órgão>/content/             produtos · referências · consultas · receitas · governança
DE DADOS        <órgão>/publications/        registro das edições publicadas
```

## Começar um repositório de dados

Requer [uv](https://docs.astral.sh/uv/) e acesso a este repositório.

```bash
uvx --from "git+ssh://git@github.com/Jaimenms/statera.git@v0.1.0" statera init meu-orgao \
    --name estat-exemplo --namespace br.gov.exemplo --organization "Estatísticas do exemplo"
cd meu-orgao
make setup          # instala o ambiente (a Statera vem pelo pyproject.toml, na versão fixada)
make validate       # o repositório nasce válido
```

Depois, crie os produtos com `make new-product NAME=<nome>` (ou `statera new-product`). O README
gerado no repositório explica o fluxo do dia a dia.

## Exemplo

[`examples/foo-bar/`](examples/foo-bar/content/README.md) é o menor repositório de dados que passa por
todo o ciclo: um produto de fonte (`foo`), um de divulgação em SQL e pandas (`bar`) e um calculado por
código legado SPSS e R (`baz`).

```bash
cd examples/foo-bar/content
uv sync && uv run statera demo
```

## O que a plataforma faz

| Fase GSBPM | Módulo | O quê |
|---|---|---|
| 1 | `projeto`, `contratos`, `classificacao`, `governanca` | Lê `statera.toml`, descobre os produtos; contratos ODCS; classificação; CODEOWNERS gerado |
| 2 | `design` | Processo em YAML espelho do BPMN 2.0; `.bpmn`, SVG e visualizador gerados |
| 4 | `collect` | Extração incremental em lotes; cortes imutáveis com manifesto |
| 5 | `process` | Projeto dbt gerado dos produtos; regras com casos (SQL e Python); código legado (SPSS pelo PSPP, R) |
| 6 | `analyse` | Contratos contra o motor; controles; controle de divulgação (supressão de células, k-anonimato) |
| 7 | `disseminate` | Pacote da edição (CSV, xlsx, sumário, Data Package, CKAN) e registro de edições |
| 8 | `evaluate` | Indicadores de qualidade e plano de ação |
| — | `consume` | Biblioteca (Ibis): catálogo, edições publicadas, cortes, métricas, consultas, receitas |

## Linha de comando

`statera init | new-product | sync | validate | conformance | run | resume | status | cuts | demo |
query | metric | recipes | rules | contracts | lineage | evaluate | docs [--interactive] | clean`.
Ver `statera --help`.

## Biblioteca

```python
from statera.consume import catalog

cat = catalog.open()
cat.search(theme="emprego-formal", layer="gold")
t = cat.table("gold.caged_saldo_uf_mensal")  # tabela Ibis (vigente)
ed = cat.edition("novo-caged", "2026-06")  # o que foi publicado, hash conferido
ed.table("saldo-uf")
ed.provenance
```

## Desenvolvimento

```bash
uv sync && uv run pytest -q && uv run ruff check .
```

Os testes da plataforma não dependem de nenhum órgão. O CI também roda o exemplo `foo-bar` de ponta a
ponta e, onde houver, a sintaxe SPSS (GNU PSPP) e os scripts R.

## Especificação

[specification/](specification/README.md): formatos de arquivo, perfis ODCS e ODPS, espaço de nomes,
classificação, processos, controles, consumo, níveis de conformidade e produtos internos.
