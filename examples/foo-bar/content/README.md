# statera-foo-bar — exemplo mínimo de repositório de conteúdo

> **Exemplo com DADOS SINTÉTICOS e nomes fictícios.** Serve para começar um repositório novo no
> padrão statera. O exemplo completo é o [`statera-trabalho-content`](../statera-trabalho-content/README.md).

O menor repositório que ainda passa por todo o ciclo: extração em lotes, corte, transformação,
controles, controle de divulgação, publicação e registro de edições. Mostra também as três formas
de calcular um produto: **SQL** (dbt), **Python** (pandas, modelo Python do dbt) e **código legado**
(sintaxe SPSS pelo GNU PSPP e script R pelo Rscript, chamados de um modelo Python).

```
Sistema Foo ──► foo (interno, fonte: lotes e cortes) ──corte──► bar (divulgação; SQL + pandas)   nível 3
                                                     ref_uf ─┤
                                                             └─► baz (divulgação; SPSS e R legados) nível 2
```

| Unidade | Para quê | Arquivos |
|---|---|---|
| [products/foo/](products/foo/1-specify/product.odps.yaml) | Extrai o Sistema Foo em lotes imutáveis e libera **cortes** | descritor, classificação, responsáveis, processo, gerador sintético, contrato bronze, staging |
| [products/bar/](products/bar/1-specify/product.odps.yaml) | Publica registros e valor por UF e mês (SQL) e a variação sobre o mês anterior ([modelo Python](products/bar/5-process/models/marts/bar/bar_variacao_uf_mensal.py), pandas) | descritor, classificação, responsáveis, calendário, processo, 2 contratos gold, 3 modelos, 2 regras com casos, 1 controle, série, sumário, 1 consulta |
| [products/baz/](products/baz/1-specify/product.odps.yaml) | Valor médio por UF, pelo [script SPSS legado](products/baz/5-process/models/marts/baz/baz_uf_mensal.sps) que o [modelo Python](products/baz/5-process/models/marts/baz/baz_uf_mensal.py) executa no GNU PSPP; e mediana por UF, pelo [script R legado](products/baz/5-process/models/marts/baz/baz_mediana_uf_mensal.R) que o [modelo Python](products/baz/5-process/models/marts/baz/baz_mediana_uf_mensal.py) executa no Rscript | nível 2: ainda sem processo to-be; **exige o PSPP e o R** (`apt install pspp r-base-core`), por isso fica fora da demonstração |
| [references/uf/](references/uf/ref_uf.odcs.yaml) | UFs (IBGE) | CSV e contrato |
| [governance/](governance/roles.yaml) | Papéis e responsáveis transversais | 2 arquivos |
| `statera.toml` | Nome, espaço de nomes `br.gov.foo-bar`, motor, registro, demonstração | 1 arquivo |

Gerados (não editar à mão): `dbt_project.yml`, `dbt/generated/`, os `.bpmn`,
`.github/CODEOWNERS` e `docs/`.

## Rodar

```bash
uv sync
uv run statera demo            # dados sintéticos, 2 edições de bar (cada uma libera antes o corte de foo)
uv run statera conformance    # nível de cada produto e o que falta para o próximo
uv run statera rules test   # casos das regras em modelos Python (os do baz exigem o PSPP e o R)
uv run statera docs --interactive   # catálogo navegável em target/static_index.html
```

As edições ficam registradas em `../publications`.

## Começar o seu a partir deste

1. Copie a pasta e troque o `[projeto]` do `statera.toml` (nome, sigla, espaço de nomes).
2. Troque `foo` pelo seu sistema de origem. O contrato bronze diz de onde extrair
   (`customProperties.extracao`), e a coluna de recepção define o corte.
3. Troque `bar` pelo seu produto de divulgação. Para criar outro produto, use
   `uv run statera new-product <nome>`: ele nasce no nível 1.
4. Rode `uv run statera sync && uv run statera validate`.
