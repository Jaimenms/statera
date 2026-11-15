# 02 — Espaço de nomes

Identificadores locais colidem quando catálogos de vários órgãos se juntam. Todo identificador
publicável **deve** levar o espaço de nomes do órgão, declarado em `statera.toml`
(`[project] namespace`, ex.: `br.gov.estat-trabalho`, no formato de domínio invertido).

O espaço de nomes identifica o **programa estatístico**, não a estrutura administrativa: **não deve**
usar a sigla do ministério nem a de secretaria, que mudam a cada reorganização do governo. Um
identificador publicado em catálogo, citado em nota técnica ou gravado na procedência de uma edição
precisa durar mais que o organograma.

| Objeto | Identificador | Onde é declarado |
|---|---|---|
| Produto | `<espaço>.<produto>` | `product.odps.yaml`, `customProperties.urn` |
| Contrato (tabela) | `<espaço>.<unidade>.<camada>.<tabela com hífens>` | contrato ODCS, `customProperties.urn` |
| Consulta | `<espaço>.query.<slug>` | `query.yaml`, `id` |
| Job OpenLineage | namespace `<espaço>`, nome `<produto>.<tarefa BPMN>` | gerado pela plataforma |

O `id` do ODCS continua curto (o padrão não aceita pontos no `id`); o identificador completo vai
no perfil. O identificador do contrato é **calculado da localização** e o CI confere que o declarado
coincide: mover um contrato de unidade muda o identificador, de propósito.

Papéis continuam locais (`equipe-caged`), ligados à unidade do SIORG em `governance/roles.yaml`.
