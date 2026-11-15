# 08 — Níveis de conformidade

Nem todo órgão tem equipe de engenharia de dados. Os níveis permitem adoção gradual (na lógica do RAP
e das cinco estrelas dos dados abertos). São cumulativos e verificados **sem dados**.

| Nível | Exige | Dá ao órgão |
|---|---|---|
| **1 — Catalogado** | `product.odps.yaml`, `classification.yaml`, `owners.yaml`; cada porta de saída com contrato no perfil (urn, classificação coerente) e aprovado; produto de divulgação: onde o público o acessa (`series.yaml` ou `distributions` declaradas). Um contrato pode declarar `manualProduction` no lugar do nó dbt | Entrar no catálogo federado; responder "o que existe e quem responde" |
| **2 — Reprodutível** | `calendar.yaml` (datas de corte); `series.yaml`; cada saída construída por um modelo dbt do repositório; ao menos uma consulta recorrente catalogada | Refazer qualquer número publicado |
| **3 — Controlado** | Regra de negócio com casos no produto ou num produto interno de que ele depende; controles; saídas que passam no gate de publicação; processo to-be executável e BPMN gerado; linhagem de toda saída até uma fonte bronze | Barrar erros antes da publicação |
| **4 — Avaliado** | `indicators.yaml` com cálculos válidos e plano de ação; `legacyParity` `done` ou `not-applicable` | Melhoria contínua mensurável |

- O produto declara o **alvo** em `product.odps.yaml` (`customProperties`, `conformanceLevel`); a
  validação exige o alvo. O nível **0** é o rascunho: é com ele que `statera new-product` cria o
  produto; a validação aceita o esqueleto e `statera conformance` mostra o que falta para o nível 1.
- `statera validate --level N` verifica só o que o nível N exige, para todos os produtos.
- `statera conformance` mostra o nível atingido e as pendências para o próximo.
- O catálogo mostra o nível atingido de cada produto (`docs/catalog.md`); o catálogo federado também deveria.
- Produtos internos (fonte e agregado) têm exigências próprias nos níveis 1 a 4 (09).
