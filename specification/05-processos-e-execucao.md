# 05 — Processos e execução

## Formato

O processo é um YAML espelho 1:1 do BPMN 2.0 (esquema `process`). Os tipos de elemento usam os
nomes da especificação OMG (`startEvent`, `serviceTask`, `userTask`, `exclusiveGateway`,
`dataStoreReference`…); o que é do padrão fica no bloco `statera` de cada tarefa:

| Atributo | Valores |
|---|---|
| `type` | `automatic` · `semi-automatic` · `manual` |
| `gsbpm` | Subprocesso(s): `4.3`, `5.1-5.7`, `6.4;7.2` |
| `duracaoMin` | Duração esperada por edição |
| `implementation` | `py:<módulo>.<função>` ou `dbt:build` |
| `models` | Com `dbt:build`: modelos a construir, com tudo o que vem antes |
| `exception` | `true` para tarefas fora do caminho normal |
| `awaits` | Num `intermediateCatchEvent`: `py:<módulo>.<função>` que diz se a espera acabou (ex.: o corte liberado, 09) |

Fluxos que saem de gateways **devem** ter `condition` (num processo executável): a situação da
última tarefa que faz o executor seguir aquele fluxo. No BPMN gerado vira
`<conditionExpression xsi:type="tFormalExpression">${situacao == 'ok'}</conditionExpression>`, que
volta ao YAML na importação. Depósitos de dados podem ter `asset` (id de contrato), usado para os
datasets de linhagem da tarefa.

## Execução

O processo to-be do produto (`2-design/processes/to-be.yaml`) é o plano de execução:

| Elemento | O executor |
|---|---|
| `startEvent` | Começa a edição (run OpenLineage da edição) |
| Tarefa automática | Roda a implementação, que devolve a situação (`ok` ou `failed`); registra duração e eventos |
| `exclusiveGateway` | Segue o fluxo cuja `condition` é a situação da última tarefa |
| Tarefa humana (manual ou semiautomática) | Para e grava o estado (`waiting`); `statera resume` conclui com `approved` (ou `rejected`, com `--reject`); a semiautomática roda a sua implementação ao ser concluída |
| Evento intermediário com `awaits` | Chama a função; se ainda não for hora, a edição fica `waiting` nele, e `statera resume` reavalia |
| Tarefa com `exception: true` | A edição fica `blocked`, com as falhas registradas |
| `endEvent` | A edição está `done` |

Uma tarefa automática que falha sem gateway depois dela também barra a edição. Implementações da
plataforma: `py:statera.collect.step`, `dbt:build`, `py:statera.analyse.step`,
`py:statera.disseminate.step`, `py:statera.evaluate.step` e `py:statera.disseminate.publish`.
Num produto interno, a execução é um **corte** (informa-se `--cut-date`, não a competência), e as
implementações próprias são `py:statera.disseminate.mark_cut` (tarefa) e
`py:statera.collect.pin_cut` (espera do consumidor), 09. Com `--with-dependencies`, a execução
libera antes os cortes que faltam nos produtos internos de entrada e reaproveita os já liberados.
Toda implementação recebe o contexto da edição e devolve a situação; um órgão pode registrar as suas.

O estado fica em `<produto>/8-evaluate/data/execucoes/<competência>.estado.json`; o log de tarefas em
`execucoes.jsonl`; os eventos OpenLineage em `eventos.jsonl`.
