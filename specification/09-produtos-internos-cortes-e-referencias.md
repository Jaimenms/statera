# 09 — Produtos internos, cortes e referências no tempo

Vários produtos de divulgação nascem da **mesma fonte**: Novo CAGED, RAIS e RAIS Mensalizada leem os
eventos do eSocial. Sem um lugar para o que é compartilhado, cada produto refaria a extração e a
consolidação, com regras que divergem aos poucos. E a fonte é **mutável**: a mesma competência muda
conforme as declarações chegam, de modo que "o dado de maio" só tem sentido com uma data de corte.

Esta especificação define três coisas: produtos **internos** para o que é compartilhado, **cortes**
imutáveis para ligar produtos no tempo, e a **temporalidade** das referências. Ela substitui o tipo de
unidade `dominio` das primeiras versões.

## Alinhamento e visibilidade

Todo produto declara no `product.odps.yaml` (`customProperties`), na terminologia do Data Mesh:

| `alignment` | O que é | `visibility` | Camada das saídas |
|---|---|---|---|
| `source` | Extração de um sistema de origem (*source-aligned*) | `internal` | bronze (fase 4) |
| `aggregate` | Entidade canônica lida por vários produtos (*aggregate*) | `internal` | silver (fase 5) |
| `consumer` (padrão) | Produto de divulgação (*consumer-aligned*) | `public` | gold (fase 5) |

Um produto de consumo **não pode** ser interno. Um produto interno **não é divulgado**: não tem série,
calendário nem gate de publicação; tem consumidores autorizados e cortes.

As **portas de entrada** do ODPS (`inputPorts`, por contrato) ligam os produtos: um produto depende
dos produtos internos donos dos contratos que ele lê. O que vale para todos os consumidores
(deduplicação entre sistemas, exclusões, prazos, classificações) **deveria** ficar no agregado; o
escopo de cada produto (por exemplo, só celetistas) fica no consumidor.

## Consumidores autorizados

Um produto interno **deve** declarar quem pode lê-lo e com que finalidade (LGPD, art. 6º, I):

```yaml
customProperties:
  - property: authorizedConsumers
    value:
      - {product: novo-caged, purpose: "estatística mensal da movimentação celetista"}
      - {query: br.gov.estat-trabalho.query.admissoes-por-secao-municipio, purpose: "atendimento a pedido, agregado"}
      - {role: assessoria-estudos, purpose: "estudo sobre rotatividade", until: "2027-11-30", act: "Despacho nº 1/2026"}
```

Uma **equipe** que não é a dona do produto pode ser autorizada a lê-lo diretamente, com `role` (de
`governance/roles.yaml`), `purpose`, `until` (prazo de validade) e `act` (quem autorizou). A leitura é
no ambiente do órgão, sem download; o que sai dela passa pelo controle de divulgação (nos termos dos
*Five Safes*: pessoas, projetos, ambiente, dados e saídas seguras). A validação **deve** falhar quando
falta um desses campos, quando o papel não existe e quando o prazo venceu: a autorização é renovada
por um novo ato ou retirada. A biblioteca, aberta com o papel (`catalog.open(role=…)` ou
`STATERA_PAPEL`), nega a leitura sem autorização vigente. Em motores com papéis (PostgreSQL, Trino…),
a plataforma gera os `grants` de leitura das portas de saída a partir das autorizações vigentes; essa
é a fronteira de verdade.

A validação **deve** falhar quando um produto (pelas portas de entrada) ou uma consulta recorrente
(pelas `bases`) lê um produto interno sem estar na lista. No dbt, a plataforma gera um `group` por
produto e o `access` de cada modelo: as portas de saída e o staging de um produto interno são
`public`; os demais modelos dele, `private`; os modelos de produtos de consumo, `protected`.

## Lotes

Um produto de fonte extrai de forma **incremental**. Cada execução grava um **lote** imutável com o que
foi recebido desde a última carga até a data de corte, em
`4-collect/data/bronze/<tabela>/batch=NNNNNN/parte-0.parquet`, com `_metadados.json` (janela de
recepção, linhas, SHA-256, hora). Cada registro é guardado uma vez. O contrato do bronze declara:

| Propriedade | Valor |
|---|---|
| `extraction` | `{source, receivedAtColumn, sourceSystem}`: de onde extrair e qual coluna data a recepção |
| coluna `batch` | Número do lote, gravado pela plataforma |
| `slaProperties` `retention` | Por quanto tempo os lotes são guardados |

Os modelos staging filtram o corte com a macro da plataforma
`{{ statera.at_cut('<coluna de recepção>', '<tabela>') }}`: recebido até a data de corte **e**
presente até o lote máximo do corte.

## Cortes

Um **corte** é o que um produto interno libera para uma data de corte. É um manifesto **imutável** em
`7-disseminate/data/<data de corte>/cut.json`, escrito pela tarefa `py:statera.disseminate.mark_cut`
depois que os contratos passaram no corte:

| Campo | Conteúdo |
|---|---|
| `product`, `urn`, `alignment`, `cutDate` | O que foi liberado |
| `run`, `markedAt`, `codeVersion` | Quem calculou, quando e com que código |
| `authorizedConsumers` | Para quem |
| `sources` (produto de fonte) | Por tabela: contrato, sistema de origem, lote máximo, linhas no corte, hash de cada lote usado e hash do conjunto |
| `outputs` (agregado) | Por porta de saída: contrato, tabela, linhas na partição do corte |
| `references` (agregado) | Versão do contrato, temporalidade, autoridade e hash de cada referência usada |
| `inputs` | Os manifestos dos cortes dos produtos internos de entrada |

Regras:

- Um corte **não deve** ser reescrito. Recalcular exige `--force` e fica registrado na execução.
- As saídas de um agregado **devem** ser guardadas por corte (`customProperties.partitionedBy:
  data_corte` no contrato, com a coluna de data de corte de `[conventions] cut_date_column`; no dbt,
  materialização incremental com `unique_key = 'data_corte'`), para
  que recalcular um corte não apague os outros. Os testes de unicidade valem dentro do corte
  (`statera.unique_at_cut`).
- Um consumidor **espera** o corte num evento intermediário do processo
  (`statera.awaits: py:statera.collect.pin_cut`, 05) e o **fixa** na edição: lê só a partição
  daquele corte, e os lotes até o lote do manifesto.
- Dois consumidores que pedem a mesma data leem o **mesmo** corte. Um corte já liberado é
  reaproveitado, sem nova extração nem recálculo.
- Um corte barrado na validação não é liberado: os consumidores continuam esperando.

Na demonstração, `statera run --with-dependencies` libera antes, em ordem, os cortes que faltam;
em produção, isso é papel do orquestrador, e a liberação do corte é o evento que dispara os
consumidores. O armazenamento equivalente em produção são tabelas Apache Iceberg com uma *tag* por
corte.

## Procedência

O Data Package de cada edição (04) registra em `x-statera`:

- `cuts`: produto interno, data de corte e execução de cada corte lido;
- `extractions`: por fonte, a data de corte, o lote máximo, as linhas e o hash, reunidos dos manifestos;
- `references`: cada referência usada, com versão do contrato, temporalidade, autoridade e hash. As
  usadas dentro de um produto interno vêm do manifesto do corte (campo `via`), com o hash do momento
  em que o corte foi calculado.

## Referências no tempo

Referências mudam: município novo é instalado, a CNAE ganha versão, a CBO é atualizada. Todo contrato
de referência **deve** declarar `customProperties.temporality`:

| Temporalidade | Significado | Exigências | Uso |
|---|---|---|---|
| `stable` | A mesma tabela vale para todo o período | — | Junção simples |
| `validity` | Cada linha vale num intervalo | Colunas de início e fim da vigência, as de `[conventions]` `valid_from_column` e `valid_to_column` (ex.: `vigencia_inicio` e `vigencia_fim`; fim vazio = vigente); intervalos da mesma chave sem sobreposição (teste gerado) | Junção pela data do evento: `data_evento >= vigencia_inicio and (vigencia_fim is null or data_evento <= vigencia_fim)` |
| `versioned` | A autoridade publica versões que não se misturam | Versão no contrato; `temporalNote` com a política | O consumidor fixa a versão; entre versões, usa a tabela de correspondência da autoridade |

`authority` diz quem mantém a referência. Para séries municipais longas, as Áreas Mínimas
Comparáveis resolvem desmembramentos; uma série que mude de versão de classificação **deveria**
documentar a correspondência usada na nota técnica.

## Conformidade

Os níveis (08) valem para produtos internos com estas exigências no lugar das de divulgação:

| Nível | Produto interno |
|---|---|
| 1 | Como os demais, mais `authorizedConsumers` |
| 2 | Fonte: contrato com `extraction` e retenção. Agregado: saídas construídas por modelos dbt e guardadas por corte (`partitionedBy`) |
| 3 | Processo executável; contratos com regras de qualidade; agregado: regras com casos e linhagem até o bronze |
| 4 | Indicadores de serviço (atraso do corte, completude) |
