# Bar — registros e valor por UF (DADOS SINTÉTICOS) — edições publicadas

> Página GERADA pelo processo a cada edição publicada. Não edite à mão. **DADOS SINTÉTICOS.**

Índice da série: [editions.csv](editions.csv).

| Competência | Versão | Data de corte | Publicado em | Retifica | Registro |
|---|---|---|---|---|---|
| 2026-01 | 1.0.0 | 2026-02-10 | 2026-11-15T01:31:12+00:00 |  | [2026-01.datapackage.json](2026-01.datapackage.json) |
| 2026-02 | 1.0.0 | 2026-03-10 | 2026-11-15T01:31:14+00:00 |  | [2026-02.datapackage.json](2026-02.datapackage.json) |

## Revisões entre as edições 2026-01 e 2026-02

Porta `por-uf`, datas de corte 2026-02-10 e 2026-03-10 (controle CK-01).

| competencia | sigla_uf | registros_anterior | registros |
|---|---|---|---|
| 2026-01 | BA | 55 | 59 |
| 2026-01 | DF | 34 | 38 |
| 2026-01 | MG | 103 | 123 |
| 2026-01 | PE | 23 | 31 |
| 2026-01 | RJ | 73 | 87 |
| 2026-01 | RS | 59 | 68 |
| 2026-01 | SP | 192 | 230 |

## Linhagem por versão — edição 2026-02 (versão 1.0.0)

Data de corte **2026-03-10**, execução OpenLineage `66b3cfa2-f79b-4975-be89-0ec84c8de47c`, código `sem-git`. Cada arquivo publicado é rastreável até os lotes exatos do bronze:

| Produto interno de entrada | Corte | Execução que liberou o corte |
|---|---|---|
| foo | 2026-03-10 | `1a3134a5-4790-4fca-a8ea-fddf93470de7` |

| Extração (bronze) | Sistema de origem | Data de corte | Até o lote | Linhas | SHA-256 |
|---|---|---|---|---|---|
| foo_registros | Sistema Foo | 2026-03-10 | 2 | 1104 | `08a0963faf963798…` |

| Referência | Versão do contrato | Temporalidade | Autoridade | Usada em | SHA-256 |
|---|---|---|---|---|---|
| ref_uf | 1.0.0 | stable | IBGE | (o próprio produto) | `d06d4fb32c2779e6…` |

| Arquivo publicado | Contrato | SHA-256 |
|---|---|---|
| por-uf.csv | gold-bar-uf-mensal | `5075c9a96544c130…` |
| variacao-uf.csv | gold-bar-variacao-uf-mensal | `db822578aaf01e23…` |
| bar-2026-02.xlsx |  | `77101d93015f3613…` |
| summary.md |  | `23c2c6d08ee3f69c…` |

## Duração observada das tarefas automáticas

Do log de execuções do processo, para comparar com a duração modelada no BPMN.

| Tarefa BPMN | Execuções | Média (s) | Máximo (s) |
|---|---|---|---|
| Task_Controles | 2 | 0.1 | 0.1 |
| Task_Pacote | 2 | 0.1 | 0.1 |
| Task_Transformar | 2 | 2.1 | 2.1 |
