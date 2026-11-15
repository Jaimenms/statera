"""Gerador de dados SINTÉTICOS do Sistema Foo (gancho [demo] sintetico do statera.toml).

Escreve a "origem" que a extração lê: um Parquet com um registro por solicitação, com a data do fato
e a data de recepção (alguns chegam com atraso, para que a mesma competência mude entre cortes).
"""

from __future__ import annotations

import csv
import random
from datetime import date, datetime, timedelta

import pandas as pd

SEMENTE = 20261114
UFS_COM_PESO = {"SP": 8, "MG": 4, "RJ": 3, "BA": 2, "RS": 2, "PE": 1, "DF": 1}


def gerar(projeto, falha: str | None = None, referencias: bool = False) -> dict[str, int]:
    rng = random.Random(SEMENTE)
    ufs = {u["sigla_uf"]: int(u["cod_uf"]) for u in csv.DictReader((projeto.raiz / "references/uf/ref_uf.csv").open())}
    siglas, pesos = list(UFS_COM_PESO), list(UFS_COM_PESO.values())
    linhas = []
    for i in range(1, 1201):
        fato = date(2026, 1, 1) + timedelta(days=rng.randrange(0, 59))  # janeiro e fevereiro
        atraso = rng.choice([1, 2, 3, 5, 8, 15, 40])  # alguns chegam depois do corte
        sigla = rng.choices(siglas, pesos)[0]
        linhas.append(
            {
                "id_registro": f"R{i:05d}",
                "id_pessoa": f"P{rng.randrange(1, 900):04d}",
                "cod_uf": ufs[sigla],
                "data_fato": fato,
                "data_recepcao": datetime.combine(fato + timedelta(days=atraso), datetime.min.time()),
                "valor": round(rng.uniform(50, 500), 2),
            }
        )
    destino = projeto.produto("foo").dados(4) / "source"
    destino.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(linhas).to_parquet(destino / "foo_registros.parquet", index=False)
    return {"foo_registros": len(linhas)}
