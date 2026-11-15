"""Variação de registros por UF em relação à competência anterior (modelo Python do dbt, com pandas).

  RN-BAR-002  variação percentual sobre o mês anterior da mesma UF; vazia na primeira competência

A regra fica numa função pura, `transform(inputs, variables)`, que a plataforma testa com os
casos da regra (statera rules test). `model()` só liga a função ao dbt.
"""

import pandas as pd


def transform(inputs: dict[str, pd.DataFrame], variables: dict) -> pd.DataFrame:
    df = inputs["bar_uf_mensal"][["competencia", "sigla_uf", "data_corte", "registros"]].copy()
    df = df.sort_values(["sigla_uf", "competencia"]).reset_index(drop=True)
    df["data_corte"] = pd.to_datetime(df["data_corte"]).dt.date  # o pandas devolve TIMESTAMP; o contrato promete DATE
    # RN-BAR-002
    df["registros_mes_anterior"] = df.groupby("sigla_uf")["registros"].shift(1)
    df["variacao_pct"] = ((df["registros"] / df["registros_mes_anterior"] - 1) * 100).round(2)
    return df


def model(dbt, session):
    dbt.config(materialized="table", meta={"rules": ["RN-BAR-002"], "gsbpm": "5.7"})
    return transform({"bar_uf_mensal": dbt.ref("bar_uf_mensal").df()}, {})
