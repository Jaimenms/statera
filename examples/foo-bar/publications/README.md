# Registro das edições publicadas — Foo Bar (exemplo mínimo)

Este repositório é escrito **pelo processo de produção** (tarefa de publicação do processo to-be,
executada por `statera`), por commit automático, a cada edição publicada. Não se edita à mão. O
código, os contratos e o desenho das séries ficam no repositório de conteúdo (`content`);
aqui fica só o histórico de edições:

| Arquivo | Conteúdo |
|---|---|
| `<serie>/<competência>.datapackage.json` | Data Package da edição: arquivos, hashes, data de corte |
| `<serie>/editions.csv` | Índice da série: uma linha por edição (e por retificação) |
| `<serie>/evaluations/<competência>.json` e `.md` | Indicadores de qualidade da edição (GSBPM 8) |
| `<serie>/README.md` | Página gerada: edições, revisões, linhagem por versão, duração |

Os arquivos de dados publicados ficam no armazenamento de publicação (portal, dados.gov.br); aqui
ficam os metadados e os hashes que provam o que foi publicado.

> Demonstração com DADOS SINTÉTICOS.
