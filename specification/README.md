# Especificação statera — versão 0.1 (rascunho)

Esta especificação diz **como um órgão organiza os seus produtos estatísticos num repositório** para
que qualquer ferramenta que a implemente (a plataforma `statera` é a implementação de referência)
consiga validá-los, executá-los, documentá-los e federar o catálogo. Ela não inventa formatos onde já
existe padrão: usa ODCS, ODPS, Frictionless Data Package, OpenLineage, BPMN 2.0, GSBPM e o dbt, e só
define **perfis** sobre eles e o que falta entre eles.

| Documento | Assunto |
|---|---|
| [01 — Repositório de conteúdo](01-repositorio-de-conteudo.md) | Layout do repositório (`content/`, `publications/`, `docs/`), `statera.toml`, unidades, fases, pastas de dados, arquivos gerados |
| [02 — Espaço de nomes](02-espaco-de-nomes.md) | Identificadores únicos entre órgãos |
| [03 — Classificação](03-classificacao.md) | Compartilhamento, natureza, sigilo e controle de divulgação; o gate de publicação |
| [04 — Perfis dos padrões](04-perfis.md) | O que o padrão exige a mais de ODCS, ODPS, Data Package e OpenLineage |
| [05 — Processos e execução](05-processos-e-execucao.md) | YAML espelho do BPMN; o processo to-be como plano de execução |
| [06 — Controles, indicadores e séries](06-controles-indicadores-series.md) | Formatos declarativos das fases 6, 7 e 8 |
| [07 — Consumo](07-consumo.md) | Consultas recorrentes, métricas, receitas e biblioteca |
| [08 — Níveis de conformidade](08-niveis-de-conformidade.md) | Adoção gradual: catalogado, reprodutível, controlado, avaliado |
| [09 — Produtos internos, cortes e referências no tempo](09-produtos-internos-cortes-e-referencias.md) | Alinhamento e visibilidade dos produtos; consumidores autorizados; lotes, cortes e procedência; temporalidade das referências |


Os esquemas (JSON Schema) estão em `src/statera/schemas/statera/` (próprios) e
`src/statera/schemas/standards/` (oficiais, copiados na versão adotada).

## Versionamento e mudança

- **SemVer da especificação.** Mudança incompatível só em versão maior, com janela de migração e
  conversor quando possível. O repositório de conteúdo declara a versão que segue
  (`content/statera.toml`, `[project] specification`).
- **Processo de mudança por RFC**, como nos padrões da Bitol (ODCS, ODPS): proposta pública, período
  de comentários, decisão registrada. Instância decisória a definir.
- **Implementação de referência e testes de conformidade públicos**: a plataforma e o seu CI.
- Termos normativos: **deve** (obrigatório no nível indicado), **deveria** (recomendado), **pode**.
