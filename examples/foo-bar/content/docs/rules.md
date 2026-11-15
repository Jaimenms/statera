# Regras de negócio

> ARQUIVO GERADO pela plataforma Statera. Não edite à mão: a próxima geração desfaz a mudança.  
> Gerado por `statera docs`. Fontes: metadados do repositório (contratos, produtos, processos, regras, séries).  
> Para mudar: edite as fontes e rode `statera docs`.  
> **Exemplo com DADOS SINTÉTICOS.**

Cada regra tem definição em prosa (`rule.md`) e **casos executáveis** (`cases.yaml`), na unidade que a aplica: no produto interno agregado, se vale para todos os consumidores; no produto de divulgação, se define o escopo dele. Os casos viram *unit tests* do dbt (`statera rules sync`).

| Regra | Unidade | Modelo dbt | Casos |
|---|---|---|---|
| [RN-BAR-001 — Competência do fato](../products/bar/5-process/rules/RN-BAR-001-competencia-do-fato/rule.md) | bar | `bar_uf_mensal` | 1 |
| [RN-BAR-002 — Variação sobre o mês anterior](../products/bar/5-process/rules/RN-BAR-002-variacao-mensal/rule.md) | bar | `bar_variacao_uf_mensal` | 1 |
| [RN-BAZ-001 — Valor médio por UF (script SPSS legado)](../products/baz/5-process/rules/RN-BAZ-001-valor-medio-legado/rule.md) | baz | `baz_uf_mensal` | 1 |
| [RN-BAZ-002 — Mediana do valor por UF (script R legado)](../products/baz/5-process/rules/RN-BAZ-002-mediana-legado/rule.md) | baz | `baz_mediana_uf_mensal` | 1 |

## RN-BAR-001 — Competência do fato

Implementação: `bar_uf_mensal` · [cases](../products/bar/5-process/rules/RN-BAR-001-competencia-do-fato/cases.yaml)

- **conta_no_mes_do_fato** — Dois registros de janeiro (um recebido em fevereiro) e um de fevereiro, em SP.

  <details><summary>entradas e saída esperada</summary>

  ```yaml
  inputs:
    int_bar__registros:
    - id_registro: R1
      cod_uf: 35
      competencia: 2026-01
      valor: 100.0
      data_corte: '2026-03-10'
    - id_registro: R2
      cod_uf: 35
      competencia: 2026-01
      valor: 300.0
      data_corte: '2026-03-10'
    - id_registro: R3
      cod_uf: 35
      competencia: 2026-02
      valor: 50.0
      data_corte: '2026-03-10'
    ref_uf:
    - cod_uf: 35
      sigla_uf: SP
      nome_uf: São Paulo
  expected:
  - competencia: 2026-01
    sigla_uf: SP
    registros: 2
    valor_total: 400.0
    valor_medio: 200.0
  - competencia: 2026-02
    sigla_uf: SP
    registros: 1
    valor_total: 50.0
    valor_medio: 50.0
  ```

  </details>

## RN-BAR-002 — Variação sobre o mês anterior

Implementação: `bar_variacao_uf_mensal` · [cases](../products/bar/5-process/rules/RN-BAR-002-variacao-mensal/cases.yaml)

- **variacao_e_primeiro_mes_vazio** — SP passa de 50 para 60 registros (+20%); a primeira competência fica sem variação.

  <details><summary>entradas e saída esperada</summary>

  ```yaml
  inputs:
    bar_uf_mensal:
    - competencia: 2026-01
      sigla_uf: SP
      data_corte: '2026-03-10'
      registros: 50
    - competencia: 2026-02
      sigla_uf: SP
      data_corte: '2026-03-10'
      registros: 60
    - competencia: 2026-01
      sigla_uf: RJ
      data_corte: '2026-03-10'
      registros: 20
  expected:
  - competencia: 2026-01
    sigla_uf: SP
    variacao_pct: null
  - competencia: 2026-02
    sigla_uf: SP
    variacao_pct: 20.0
  - competencia: 2026-01
    sigla_uf: RJ
    variacao_pct: null
  ```

  </details>

## RN-BAZ-001 — Valor médio por UF (script SPSS legado)

Implementação: `baz_uf_mensal` · [cases](../products/baz/5-process/rules/RN-BAZ-001-valor-medio-legado/cases.yaml)

- **media_por_uf_e_mes** — Dois registros de SP em janeiro (10 e 30) dão média 20; um de RJ (5) dá 5.

  <details><summary>entradas e saída esperada</summary>

  ```yaml
  inputs:
    int_baz__registros:
    - competencia: 2026-01
      cod_uf: 35
      valor: 10.0
    - competencia: 2026-01
      cod_uf: 35
      valor: 30.0
    - competencia: 2026-01
      cod_uf: 33
      valor: 5.0
    ref_uf:
    - cod_uf: 35
      sigla_uf: SP
    - cod_uf: 33
      sigla_uf: RJ
  expected:
  - competencia: 2026-01
    sigla_uf: SP
    registros: 2
    valor_medio: 20.0
  - competencia: 2026-01
    sigla_uf: RJ
    registros: 1
    valor_medio: 5.0
  ```

  </details>

## RN-BAZ-002 — Mediana do valor por UF (script R legado)

Implementação: `baz_mediana_uf_mensal` · [cases](../products/baz/5-process/rules/RN-BAZ-002-mediana-legado/cases.yaml)

- **mediana_impar_e_par** — Três registros de SP (10, 30, 20) dão mediana 20; dois de RJ (5, 8) dão 6,5.

  <details><summary>entradas e saída esperada</summary>

  ```yaml
  inputs:
    int_baz__registros:
    - competencia: 2026-01
      cod_uf: 35
      valor: 10.0
    - competencia: 2026-01
      cod_uf: 35
      valor: 30.0
    - competencia: 2026-01
      cod_uf: 35
      valor: 20.0
    - competencia: 2026-01
      cod_uf: 33
      valor: 5.0
    - competencia: 2026-01
      cod_uf: 33
      valor: 8.0
    ref_uf:
    - cod_uf: 35
      sigla_uf: SP
    - cod_uf: 33
      sigla_uf: RJ
  expected:
  - competencia: 2026-01
    sigla_uf: SP
    valor_mediano: 20.0
  - competencia: 2026-01
    sigla_uf: RJ
    valor_mediano: 6.5
  ```

  </details>
