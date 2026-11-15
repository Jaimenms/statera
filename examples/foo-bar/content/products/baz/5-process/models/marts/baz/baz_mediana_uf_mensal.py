"""Baz: o script R legado `baz_mediana_uf_mensal.R`, executado pelo Rscript dentro de um modelo Python do dbt.

  RN-BAZ-002  mediana do valor por UF e competência (no script legado)

O Python só prepara as inputs, chama o R e acrescenta o que o legado não fazia (sigla da UF e data
de corte). A regra continua no `.R`, sem reescrita. Os casos da regra rodam pela plataforma
(`statera rules test`) onde o R existe; o CI o instala e os exige.
"""

from pathlib import Path

import pandas as pd
from statera.legacy import r

SCRIPT = Path(__file__).with_name("baz_mediana_uf_mensal.R")


def transform(inputs: dict[str, pd.DataFrame], variables: dict) -> pd.DataFrame:
    registros = inputs["int_baz__registros"][["competencia", "cod_uf", "valor"]].astype({"valor": float})
    mediana = r.run(SCRIPT, {"registros": registros}, saida="resultado")
    mediana = mediana.astype({"cod_uf": int})
    ufs = inputs["ref_uf"][["cod_uf", "sigla_uf"]].astype({"cod_uf": int})
    saida = mediana.merge(ufs, on="cod_uf", how="left")
    saida["data_corte"] = pd.to_datetime(variables["data_corte"]).date()
    saida["valor_mediano"] = saida["valor_mediano"].round(2)
    return saida[["competencia", "sigla_uf", "data_corte", "valor_mediano"]]


def model(dbt, session):
    dbt.config(materialized="table", meta={"rules": ["RN-BAZ-002"], "gsbpm": "5.7", "legacy": "r"})
    # o dbt lê os dbt.ref() do código para montar a linhagem: os nomes têm de ser literais
    inputs = {"int_baz__registros": dbt.ref("int_baz__registros").df(), "ref_uf": dbt.ref("ref_uf").df()}
    return transform(inputs, {"data_corte": dbt.config.get("data_corte")})
