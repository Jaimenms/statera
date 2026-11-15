"""Métricas definidas uma vez (camada semântica mínima).

As métricas de cada unidade ficam em `<unidade>/5-process/metrics.yaml`, num formato inspirado
na camada semântica do dbt (MetricFlow, Apache 2.0 desde a versão 0.209.0):

    simples   agregação (soma, media, minimo, maximo, contagem) de uma expressão sobre a fonte
    razao     numerador / denominador (outras métricas), vezes um fator

Cada métrica simples lista as FONTES de que pode ser calculada, com as dimensões que cada uma
permite; a plataforma escolhe a primeira fonte que cobre as dimensões e os filtros pedidos e gera
o SQL. Valores de filtro vão como parâmetros do motor, nunca concatenados. Em produção, o mesmo
YAML pode ser convertido em `semantic_models` e `metrics` do MetricFlow (ver ADR 0013).
"""

from __future__ import annotations

import re

import duckdb
import pandas as pd

from statera import classificacao, contratos
from statera.projeto import Projeto, atual

AGREGACOES = {"sum": "sum", "average": "avg", "min": "min", "max": "max", "count": "count"}
IDENTIFICADOR = re.compile(r"^[a-z_][a-z0-9_]*$")


class MetricError(ValueError):
    """Métrica inexistente, dimensão não permitida ou filtro inválido."""


def definitions(projeto: Projeto | None = None) -> dict[str, dict]:
    projeto = projeto or atual()
    saida = {}
    for u in projeto.unidades:
        for m in (u.ler("5-process/metrics.yaml") or {}).get("metrics", []):
            saida[m["name"]] = {**m, "unit": u, "table": ", ".join(f["table"] for f in m.get("sources", []))}
    return saida


def _fonte(m: dict, dims: set[str]) -> dict:
    for fonte in m["sources"]:
        if dims <= set(fonte["dimensions"]):
            return fonte
    raise MetricError(f"{m['name']}: nenhuma fonte permite as dimensões {sorted(dims)}")


def _sql_simples(m: dict, por: list[str], periodo: tuple[str, str] | None, filtros: dict) -> tuple[str, list, dict]:
    for nome in [*por, *filtros]:
        if not IDENTIFICADOR.match(nome):
            raise MetricError(f"dimensão inválida: {nome!r}")
    fonte = _fonte(m, set(por) | set(filtros))
    tempo = m["time"]
    grupos = ", ".join([tempo, *por])
    condicoes, parametros = [], []
    if periodo:
        condicoes.append(f"{tempo} between ? and ?")
        parametros += list(periodo)
    for coluna, valor in filtros.items():
        condicoes.append(f"{coluna} = ?")
        parametros.append(valor)
    onde = f" where {' and '.join(condicoes)}" if condicoes else ""
    sql = (
        f"select {grupos}, {AGREGACOES[m['aggregation']]}({m['expression']}) as {m['name']} "
        f"from {fonte['table']}{onde} group by {grupos} order by {grupos}"
    )
    return sql, parametros, fonte


def compute(
    nome: str,
    por: list[str] | None = None,
    competencia: str | None = None,
    competencias: tuple[str, str] | None = None,
    filtros: dict | None = None,
    projeto: Projeto | None = None,
) -> pd.DataFrame:
    """Calcula a métrica; o DataFrame traz a procedência em `df.attrs['procedencia']`."""
    projeto = projeto or atual()
    todas = definitions(projeto)
    if nome not in todas:
        raise MetricError(f"métrica '{nome}' não existe; disponíveis: {sorted(todas)}")
    m, por, filtros = todas[nome], list(por or []), dict(filtros or {})
    periodo = (competencia, competencia) if competencia else competencias
    if m["type"] == "ratio":
        num = compute(m["numerator"], por, competencia, competencias, filtros, projeto)
        den = compute(m["denominator"], por, competencia, competencias, filtros, projeto)
        chaves = [m["time"], *por]
        df = num.merge(den, on=chaves)
        df[nome] = (df[m["numerator"]] / df[m["denominator"]].where(df[m["denominator"]] != 0)) * m.get("factor", 1)
        df = df[[*chaves, nome]]
        df.attrs["procedencia"] = {"metrica": nome, "componentes": [num.attrs["procedencia"], den.attrs["procedencia"]]}
        return df
    sql, parametros, fonte = _sql_simples(m, por, periodo, filtros)
    contrato = contratos.por_tabela(projeto).get(fonte["table"])
    con = duckdb.connect(str(projeto.warehouse), read_only=True)
    try:
        df = con.execute(sql, parametros).df()
        cortes = con.execute(f"select distinct cast(data_corte as varchar) from {fonte['table']}").fetchall()
    finally:
        con.close()
    valores = df[nome].dropna()
    if len(valores) and valores.map(lambda v: float(v).is_integer()).all():
        df[nome] = df[nome].astype("Int64")  # somas de inteiros voltam inteiras
    df.attrs["procedencia"] = {
        "metrica": nome,
        "source": fonte["table"],
        "contract": contrato["id"] if contrato else None,
        "classification": classificacao.resumo(classificacao.da_tabela(contrato)) if contrato else None,
        "cut_dates": sorted(c[0] for c in cortes),
        "sql": sql,
    }
    return df
